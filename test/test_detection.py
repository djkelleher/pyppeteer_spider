from distbot.spider import Spider
import pytest
import pytest_asyncio

test_url = "https://www.amazon.com/"

pytestmark = pytest.mark.asyncio


async def test_fingerprint():
    spider = Spider()
    await spider.add_browser(launch_options={'headless': True})
    _, page = await spider.get('https://bot.sannysoft.com/')
    await page.screenshot(path='browser_fingerprint.png')


async def test_user_agent():
    spider = Spider()
    await spider.add_browser(launch_options={'headless': True})
    _, page = await spider.get(test_url)
    user_agent = await page.evaluate('navigator.userAgent')
    assert (user_agent in spider.user_agents)
    await spider.shutdown()


async def test_webdriver_visible():
    spider = Spider()
    await spider.add_browser(launch_options={'headless': True})
    _, page = await spider.get(test_url)
    webdriver = await page.evaluate('navigator.webdriver')
    assert (webdriver is None)
    await spider.shutdown()


async def test_window_chome():
    spider = Spider()
    await spider.add_browser(launch_options={'headless': True})
    _, page = await spider.get(test_url)
    window_chrome = await page.evaluate('window.chrome')
    assert (window_chrome is not None)
    await spider.shutdown()


async def test_navigator_permissions():
    spider = Spider()
    await spider.add_browser(launch_options={'headless': True})
    _, page = await spider.get(test_url)
    permissions = await page.evaluate("""() => {
    const permissionStatus = navigator.permissions.query({ name: 'notifications' });
    return !(Notification.permission === 'denied' && permissionStatus.state === 'prompt')
  }""")
    assert (len(permissions))
    await spider.shutdown()


async def test_plugins():
    spider = Spider()
    await spider.add_browser(launch_options={'headless': True})
    _, page = await spider.get(test_url)
    plugins = await page.evaluate('navigator.plugins.length')
    assert (len(plugins))
    await spider.shutdown()


async def test_languages():
    spider = Spider()
    await spider.add_browser(launch_options={'headless': True})
    _, page = await spider.get(test_url)
    languages = await page.evaluate('navigator.languages')
    assert (languages == ['en-US', 'en'])
    await spider.shutdown()


async def test_console_debug():
    spider = Spider()
    await spider.add_browser(launch_options={'headless': True})
    _, page = await spider.get(test_url)
    console = await page.evaluate("() => console.debug('foo')")
    assert (console is None)
    await spider.shutdown()
