from sanic import Sanic, response
import pyppeteer.launcher
from pyppeteer.browser import Browser

from typing import Dict
from pathlib import Path
from pprint import pformat
import asyncio
import argparse
import logging
import re


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-a',
                        '--address',
                        type=str,
                        default='0.0.0.0',
                        help='Address to run the server on.')
    parser.add_argument('-p',
                        '--port',
                        type=int,
                        default='80',
                        help='Port to run the server on.')
    return parser.parse_args()


# map DevTools endpoint to Browser
active_browsers = {}


app = Sanic("Browser Server")


@ app.route('/new_browser')
async def new_browser(request):
    """Open a browser."""
    # launch new browser with launch options from request.
    launch_options = request.json
    logging.info(f"Starting browser: {pformat(launch_options)}")
    browser = await pyppeteer.launcher.launch(launch_options)
    # save reference to Browser.
    active_browsers[browser.wsEndpoint] = browser
    # Return the DevTools WebSocket endpoint so a remote client can connect.
    return response.json({
        'dev_tools': browser.wsEndpoint,
        'launch_opts': request.json})


@ app.route('/browsers')
async def browsers(request):
    browsers_ws = list(active_browsers.keys())
    logging.info(f"Active browsers: {pformat(browsers_ws)}")
    return response.json(browsers_ws)


@ app.route('/remove_browser')
async def remove_browser(request):
    b = request.args['browser'][0]
    if b in active_browsers:
        logging.info(f"Shutting down browser: {b}")
        try:
            await asyncio.wait_for(active_browsers[b].close(), timeout=5)
        except asyncio.TimeoutError:
            logging.warning("Could not propertly close browser.")
        del active_browsers[b]
    else:
        logging.error(f"Unknown browser endpoint: {b}")


if __name__ == '__main__':
    args = parse_args()

    app.run(host=args.address, port=args.port)
