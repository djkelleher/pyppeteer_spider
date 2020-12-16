from distbot.utils import logger, user_agents
import distbot.evasions as ev

from pyppeteer.network_manager import Request, Response
from pyppeteer.browser import Browser
from pyppeteer.page import Page
import pyppeteer.connection
import pyppeteer.launcher
import pyppeteer.errors
import requests

from typing import Dict, Tuple, List, Union, Any
from collections import defaultdict
from asyncio.locks import Lock
from datetime import datetime
from pathlib import Path
from pprint import pformat
from uuid import uuid4
import platform
import logging
import asyncio
import random
import signal
import pickle
import json
import re


class Spider:
    """Spider that distributes requests among multiple browsers/pages and performs automatic error recovery."""

    def __init__(self):
        # runtime storage containers.
        self.browsers: Dict[Browser, Any] = {}
        self.pages: Dict[Page, Any] = {}
        self.screenshot_dir: Path = None
        self.idle_page_q = asyncio.Queue()
        # use user agents that match current platform.
        self.user_agents = user_agents.get(
            platform.system(), user_agents.get("Linux"))
        self.start_time = datetime.now()

    async def add_browser(self, pages: int = 1,
                          server: str = None,
                          launch_options: Dict[str, Any] = {}) -> Browser:
        """Launch a new browser."""
        if 'proxy' in launch_options:
            self.set_launch_args_proxy(launch_options)
        # create screenshot directory if user wants screenshots.
        if launch_options.get('screenshot', False):
            self._set_screenshot_dir()
        # if server address is provided, launch browser on server.
        if server:
            browser = await self._launch_remote_browser(server, launch_options)
        else:
            # start a local browser.
            browser = await self._launch_local_browser(launch_options)
        # save browser data.
        self.browsers[browser] = {
            'page_count': pages,
            'launch_options': launch_options,
            'server': server,
            'consec_errors': 0,
            'lock': Lock(),
            'id': str(uuid4())
        }
        # add callback that will be called in case of disconnection with Chrome Dev Tools.
        browser._connection.setClosedCallback(
            self.__on_connection_close)
        # add pages (tabs) to the new browser.
        # a new browser has 1 page by default, so add 1 less than desired page count.
        for _ in range(pages-1):
            await browser.newPage()
        for page in await browser.pages():
            await self._init_page(page)

    def set_launch_args_proxy(self, launch_options: Dict[str, Any]) -> None:
        """Remove any old proxy from args and add a new proxy to args."""
        launch_options['args'] = [
            a for a in launch_options.get('args', []) if not a.startswith('--proxy-server=')] \
            + [f'--proxy-server="{launch_options["proxy"]}"']

    def current_browser_proxy(self, browser: Browser) -> Union[str, None]:
        """Get address of proxy that browser is currently using."""
        if browser in self.browsers:
            return self.browsers[browser]['launch_options'].get('proxy')

    async def get(self, url: str, retries: int = 2, **kwargs) -> Tuple[Response, Page]:
        """Navigate next idle page to url."""
        async def _get(url: str, page: Page, **kwargs) -> Response:
            """All page functions that will hang on page crash go here."""
            if 'cookies' in kwargs:
                # set request cookies if provided.
                cookies = kwargs.pop('cookies')
                set_cookies = page.setCookie(cookies) if isinstance(
                    cookies, dict) else page.setCookie(*cookies)
                await set_cookies
            # all kwargs besides 'cookies' should be for goto
            resp = await page.goto(url, **kwargs)
            if self.browsers[page.browser]['launch_options'].get('screenshot', False):
                # save screenshot of page.
                await self._take_screenshot(page)
            return resp

        async def _retry_get(url: str, retries: int, **kwargs):
            """Retry navigation if there are remaining retries."""
            retries -= 1
            if retries >= 0:
                logger.warning(
                    f"Retrying request to {url}. Retries remaining: {retries}")
                return await asyncio.create_task(
                    self.get(url, retries, **kwargs))
            logger.error(
                f"Max retries exceeded: {url}. URL can not be navigated.")

        # get next page from idle queue.
        page = await self._get_idle_page()
        browser_data = self.browsers[page.browser]
        timeout = kwargs.get(
            'timeout', self._default_nav_func_wait(browser_data))
        try:
            resp = await asyncio.wait_for(_get(url, page, **kwargs), timeout=timeout)
        except asyncio.TimeoutError:
            # timeout suggests browser crash.
            logger.warning(
                f"Detected browser crash {page.browser} (get timeout exceeded {timeout})")
            await self.replace_browser(page.browser)
            return await _retry_get(url, retries, **kwargs)
        except Exception as e:
            logger.exception(
                f"Error fetching page {url}: {e}")
            # record that there was an error while navigating page.
            await self._log_browser_error_status(page.browser, True)
            # add the page back to idle page queue.
            await self.set_idle(page)
            return await _retry_get(url, retries, **kwargs)
        # record that page was navigated with no error.
        await self._log_browser_error_status(page.browser, False)
        status = resp.status if resp else None
        logger.info(
            f"[{status}] (server - {browser_data['server']}, browser - {browser_data['id']}, page - {self.pages[page]['id']}): {page.url}")
        return resp, page

    def _default_nav_func_wait(self, browser_data: Dict[str, Any]) -> int:
        """Default asyncio.wait_for timeout to use for functions that naviage a page."""
        # Pyppeteer's default navigation timeout is 30s. Allow waiting for 25% longer than default navigation timeout.
        default_wait_time = browser_data['launch_options'].get(
            'defaultNavigationTimeout', 30_000) * 1.25
        # Pyppeteer timout and defaultNavigationTimeout are in milliseconds, but wait_for needs seconds.
        return default_wait_time / 1_000

    async def set_idle(self, page: Page) -> None:
        """Add page to the idle queue."""
        # check that page has not been closed and page is not already idle.
        if page in self.pages and page not in self.idle_page_q._queue:
            # add page to queue.
            await self.idle_page_q.put(page)
            # mark that page is idle.
            self.pages[page]['is_idle'] = True

    async def cancel_spider_tasks(self):
        """Cancel all of Spider's tasks."""
        tasks = [t for t in asyncio.all_tasks(
        ) if t is not asyncio.current_task() and 'coro=<Spider.' in str(t)]
        [t.cancel() for t in tasks]
        logger.info(f"Cancelling {len(tasks)} outstanding tasks.")
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self, sig=None) -> None:
        """Shutdown all browsers."""
        if sig is not None:
            logger.info(f"Caught signal: {sig.name}")
        logger.info("Shutting down...")
        await self.cancel_spider_tasks()
        # close all browsers on all servers.
        await asyncio.gather(
            *[asyncio.create_task(
                self._shutdown_browser(b))
                for b in set(self.browsers.keys())])

    async def _launch_local_browser(self, launch_options: Dict[str, Any] = None) -> Browser:
        """Launch a new browser on local machine."""
        logger.info(
            f"Launching local browser:\n{pformat(launch_options)}")
        return await pyppeteer.launcher.launch(launch_options)

    async def _launch_remote_browser(self, server_ip,
                                     launch_options: Dict[str, Any] = None) -> Browser:
        """Initialize a Browser inastance and connect to the DevTools endpoint of a browser running on machine at {server_ip}."""
        logger.info(
            f"Launching remote browser on {server_ip}:\n{pformat(launch_options)}")
        endpoint = f"http://{server_ip}/new_browser"
        resp = requests.get(endpoint, json=launch_options)
        if resp.status_code != 200:
            logger.error(
                f"Could not add browser ({resp.status_code}): {endpoint}")
            return
        logger.info(
            f"[{resp.status_code}] Added Browser on {server_ip}")
        # construct DevTools endpoint.
        dev_tools_endpoint = resp.json()['dev_tools'].replace(
            '127.0.0.1', server_ip.split(':')[0])
        logger.info(
            f"Connecting to {server_ip} browser: {dev_tools_endpoint}")
        # connect to new browser's DevTools endpoint.
        browser = await pyppeteer.launcher.connect(browserWSEndpoint=dev_tools_endpoint)
        logger.info(f"Connected to browser {dev_tools_endpoint}: {browser}")
        return browser

    async def _init_page(self, page: Page) -> None:
        """Initialize a new page."""
        self.pages[page] = {
            'id': str(uuid4()),
            'is_idle': False
        }
        # add custom settings to page.
        await self._add_page_settings(page)
        # add page to idle queue.
        await self.set_idle(page)
        # start task to periodically check page idle status.
        asyncio.create_task(
            self._check_idle_status(page))

    async def _get_idle_page(self) -> Page:
        """Get next page from the idle queue and check if the browser this page belongs to has crashed."""
        # block until a page is available.
        page = await self.idle_page_q.get()
        # mark time that we've seen page is idle.
        self.pages[page]['is_idle'] = False
        self.pages[page]['time_last_idle'] = datetime.now()
        # closed pages should not be in queue.
        if page.isClosed():
            logger.warning(
                f"Found closed page in idle queue. Replacing page {page}")
            # launch new page to replace closed page.
            page = await page.browser.newPage()
            asyncio.create_task(self._init_page(page))
            return await self._get_idle_page()
        try:
            # wait for page to set a random custom user-agent string.
            await asyncio.wait_for(page.setUserAgent(
                random.choice(self.user_agents)), timeout=3)
            if self.browsers[page.browser]['launch_options'].get('deleteCookies', False):
                # wait for page to clear cookies.
                await asyncio.wait_for(
                    page._client.send('Network.clearBrowserCookies'), timeout=3)
        except (asyncio.TimeoutError, pyppeteer.errors.NetworkError) as e:
            # all page functions will hang and time out if browser has crashed.
            # replace crashed browser.
            logger.warning(f"Detected error with browser {page.browser}: {e}")
            await self.replace_browser(page.browser)
            # try again
            return await self._get_idle_page()
        return page

    async def set_ad_block(self, page: Page, enabled: bool = True):
        # Enable Chrome's experimental ad filter on all sites.
        await page._client.send('Page.setAdBlockingEnabled', {'enabled': True})

    async def set_blocked_urls(self, page: Page, urls: List[str]):
        await page._client.send('Network.setBlockedURLs', {'urls': urls})

    async def set_stealth(self, page: Page):
        "add JavaScript functions to prevent automation detection."
        await asyncio.gather(
            page.evaluateOnNewDocument(ev.webdriver),
            page.evaluateOnNewDocument(ev.media_codecs),
            page.evaluateOnNewDocument(ev.console_debug),
            page.evaluateOnNewDocument(ev.iframe),
            page.evaluateOnNewDocument(ev.languages),
            page.evaluateOnNewDocument(ev.navigator),
            page.evaluateOnNewDocument(ev.permissions),
            page.evaluateOnNewDocument(ev.plugins),
            page.evaluateOnNewDocument(ev.webgl),
            page.evaluateOnNewDocument(ev.window_outer_dims),
        )

    async def _add_page_settings(self, page: Page) -> None:
        """Add custom settings to a page."""
        # launch options for this page.
        launch_options = self.browsers[page.browser]['launch_options']
        # set the default maximum navigation time.
        if 'defaultNavigationTimeout' in launch_options:
            page.setDefaultNavigationTimeout(
                launch_options['defaultNavigationTimeout'])
        tasks = [self.set_stealth(page)]
        # blocks URLs from loading.
        if 'blockedURLs' in launch_options:
            tasks.append(self.set_blocked_urls(
                page, launch_options['blockedURLs']))
        # disable cache for each request.
        if 'setCacheEnabled' in launch_options:
            tasks.append(page.setCacheEnabled(
                launch_options['setCacheEnabled']))
        # add a JavaScript function(s) that will be invoked whenever the page is navigated.
        for script in launch_options.get('evaluateOnNewDocument', []):
            tasks.append(page.evaluateOnNewDocument(script))
        # intercept all request and only allow requests for types not in request_abort_types.
        request_abort_types = launch_options.get('requestAbortTypes')
        if request_abort_types:
            # enable request interception.
            tasks.append(page.setRequestInterception(True))

            async def block_type(request: Request):
                # condition(s) where requests should be aborted.
                if request.resourceType in request_abort_types:
                    await request.abort()
                elif launch_options.get('blockRedirects', False) and request.isNavigationRequest() and len(request.redirectChain):
                    await request.abort()
                else:
                    await request.continue_()

            page.on('request',
                    lambda request: asyncio.create_task(block_type(request)))
        await asyncio.gather(*tasks)

    async def _check_idle_status(self, page: Page) -> None:
        """ It's possible (but rare) for a page to hang when a user calls a page function. ex. page.xpath(). 
            This functions checks the last time a page was set idle and automatically adds the page 
            to the idle queue if that time exceeds pageIdleTimeout (default 5 mins).
        """
        # check that page has not been removed and page is not idle.
        if page in self.pages and not self.pages[page]['is_idle']:
            # check how long page has not been idle.
            t_since_idle = datetime.now() - self.pages[page]['time_last_idle']
            # check if user provided idle timout. If not, set it to 5 mins.
            idle_timeout = self.browsers[page.browser]['launch_options'].get(
                'pageIdleTimeout', 60*5)
            if t_since_idle.seconds >= idle_timeout:
                logger.error(
                    f"""Page {self.pages[page]['id']} has not been set idle in {str(t_since_idle)}.
                        Assuming client side crash. Adding page to idle queue.""")
                # set page idle so a functioning client side task can use it.
                await self.set_idle(page)
        # check page's idle status again in about another minute.
        await asyncio.sleep(60)
        asyncio.create_task(
            self._check_idle_status(page))

    async def replace_browser(self, browser: Browser, launch_options: Dict[str, Any] = None) -> None:
        """Close browser and launch a new one."""
        # check if this browser has already been replaced.
        if browser not in self.browsers:
            logger.debug(f'Browser {browser} has already been replaced.')
            return
        lock = self.browsers[browser]['lock']
        # check if another task is currently replacing this browser.
        if lock.locked():
            logger.debug(
                f'Waiting for browser {browser} replacement to finish.')
            # wait for new browser launch to finish.
            while lock.locked():
                await asyncio.sleep(0.5)
            # return now that browser replacement is complete.
            return
        # lock this browser so other tasks can not create replacement browsers for this browser.
        async with lock:
            logger.info(f"Replacing browser: {browser}.")
            browser_data = self.browsers[browser]
            # update launch options if new options are provided.
            if launch_options:
                browser_data['launch_options'].update(launch_options)
            # close the old browser.
            await self._shutdown_browser(browser)
            # add a new browser.
            await self.add_browser(pages=browser_data['page_count'],
                                   server=browser_data['server'],
                                   launch_options=browser_data['launch_options'])
        logger.info(f"Browser {browser} replacement complete.")

    async def _log_browser_error_status(self, browser: Browser, error: bool) -> None:
        """If error, increment concecutive error count snd replace browser if concecutive error count exceeds limit.
           If no error, reset concecutive error count."""
        browser_data = self.browsers.get(browser)
        # Don't record error for a browser that has already been replaced or is currently being replaced.
        if browser_data and not browser_data['lock'].locked():
            if error:
                # record error.
                browser_data['consec_errors'] += 1
                if browser_data['consec_errors'] > browser_data['launch_options'].get('maxConsecutiveError', 4):
                    return await self.replace_browser(browser)
            else:
                browser_data['consec_errors'] = 0

    async def _close_page(self, page: Page) -> None:
        """Close page and remove all references."""
        logger.info(f"Removing page: {page}")
        if page in self.idle_page_q._queue:
            # remove page from idle queue.
            self.idle_page_q._queue.remove(page)
        del self.pages[page]
        try:
            # wait for page to close.
            await asyncio.wait_for(page.close(), timeout=2)
        except asyncio.TimeoutError:
            logger.warning(
                f"Page {page} could not be properly closed.")

    async def _shutdown_browser(self, browser: Browser) -> None:
        """Close browser and remove all references."""
        logger.info(f"Removing browser: {browser}")
        # remove all pages from the browser.
        for page in await browser.pages():
            await self._close_page(page)
        # disable self.__on_connection_close
        browser._connection._closeCallback = None
        # attempt to properly close browser.
        try:
            await asyncio.wait_for(browser.close(), timeout=2)
        except asyncio.TimeoutError:
            pass
        del self.browsers[browser]

    def _set_screenshot_dir(self) -> None:
        """create screenshot directory for this Spider."""
        self.screenshot_dir = Path(
            f"distbot/screenshots_{self.start_time.strftime('%Y-%m-%d_%H:%M:%S')}")
        self.screenshot_dir.mkdir(exist_ok=True)

    async def _take_screenshot(self, page: Page) -> None:
        """take a screenshot of the current page."""
        page_id = self.pages[page]['id']
        # remove this page's old screenshot.
        for f in self.screenshot_dir.glob(f'*{page_id}.jpeg'):
            f.unlink()
        # take new screenshot.
        # quality=0 uses less CPU and still provides a pretty clear image.
        await page.screenshot(path=str(self.screenshot_dir.joinpath(
            f"{datetime.now().strftime('%Y-%m-%d_%H:%M:%S')}_{page}.jpeg")), quality=0)

    def _set_signal_handler(self) -> None:
        """catch interupt signals and call shutdown."""
        loop = asyncio.get_running_loop()
        # get interupt signals supported by user's OS.
        signals = [getattr(signal, s) for s in (
            'SIGBREAK', 'SIGINT', 'SIGTERM', 'SIGHUP') if hasattr(signal, s)]
        for s in signals:
            try:
                loop.add_signal_handler(
                    s, lambda s=s: asyncio.create_task(self.shutdown(s)))
            except NotImplementedError:
                pass

    def __on_connection_close(self) -> None:
        """Find browser(s) with closed websocket connection and replace it."""
        logger.info("Checking closed connections.")
        for browser in set(self.browsers.keys()):
            if browser._connection.connection is None or not browser._connection.connection.open:
                logger.warning(f"Found closed connection: {browser}")
                asyncio.create_task(
                    self.replace_browser(browser))
