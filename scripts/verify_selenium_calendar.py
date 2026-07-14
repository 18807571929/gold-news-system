# -*- coding: utf-8 -*-
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, r"E:\量化项目\03 Warm")
sys.stdout.reconfigure(line_buffering=True)

print("start", flush=True)

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from download_economic_calendar import INVESTING_CAL_URL, _extract_investing_payload_from_response, parse_calendar_html
    print("import ok", flush=True)

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1920,1080")

    try:
        driver = webdriver.Chrome(options=opts)
    except Exception:
        from webdriver_manager.chrome import ChromeDriverManager
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)

    d0, d1 = "2024-01-01", "2024-01-31"
    driver.get(INVESTING_CAL_URL)
    import time
    time.sleep(5)
    script = """
    var body = 'dateFrom=' + encodeURIComponent(arguments[0])
      + '&dateTo=' + encodeURIComponent(arguments[1])
      + '&country%5B%5D=5&importance%5B%5D=3&timeZone=0&timeFilter=timeRemain'
      + '&currentTab=custom&limit_from=0';
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/economic-calendar/Service/getCalendarFilteredData', false);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
    xhr.send(body);
    return xhr.responseText;
    """
    txt = driver.execute_script(script, d0, d1)
    title = driver.title
    driver.quit()
    print("page_title", title, flush=True)
    html = _extract_investing_payload_from_response(txt) or txt
    print("html_len", len(html), flush=True)
    low = html.lower()
    if "cloudflare" in low or "just a moment" in low:
        print("RESULT: FAIL cloudflare", flush=True)
        sys.exit(2)
    raw = parse_calendar_html(html, "investing")
    print("raw_rows", len(raw), flush=True)
    if len(raw) > 0:
        print("RESULT: OK", flush=True)
    else:
        print("RESULT: FAIL no rows", flush=True)
        sys.exit(3)
except Exception as e:
    print("RESULT: FAIL", repr(e), flush=True)
    sys.exit(1)
