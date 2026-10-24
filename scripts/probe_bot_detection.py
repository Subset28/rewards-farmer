import sys
import os
import time

sys.path.insert(0, "/app/src")
sys.path.insert(0, "src")

import accounts
import browser

# Use an isolated test profile so we do not touch or lock live account profiles
account = accounts.Account(name="bot_test", user_data_dir="/tmp/bot_test", profile_name="Default")

print(f"Launching Edge in container for account '{account.name}'...")
driver = browser.start_driver(account)
if not driver:
    print("FAILED to start driver")
    sys.exit(1)

try:
    print("Navigating to https://bot.sannysoft.com/ ...")
    driver.get("https://bot.sannysoft.com/")
    time.sleep(8)  # allow all client-side detection tests to execute

    # Save full-page screenshot
    screenshot_path = "/app/data-dir/bot_detection_sannysoft.png"
    driver.save_screenshot(screenshot_path)
    print(f"Screenshot saved to {screenshot_path}")

    # Extract all test results from sannysoft table
    rows = driver.find_elements("css selector", "table tr")
    results = []
    for row in rows:
        cells = row.find_elements("css selector", "td, th")
        if len(cells) >= 2:
            name = cells[0].text.strip()
            val = cells[1].text.strip()
            cl = cells[1].get_attribute("class") or ""
            results.append((name, val, cl))

    print("\n=== SANNYSOFT BOT DETECTION REPORT ===")
    for name, val, cl in results:
        status = "FAIL" if "failed" in cl.lower() or "bad" in cl.lower() else "PASS"
        if not val or not name:
            continue
        print(f"[{status}] {name}: {val}")

    # Check navigator.webdriver directly
    nav_webdriver = driver.execute_script("return navigator.webdriver")
    print(f"\nnavigator.webdriver: {nav_webdriver}")

    # Check WebGL vendor/renderer directly
    webgl_info = driver.execute_script("""
        try {
            var canvas = document.createElement('canvas');
            var gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
            if (!gl) return 'No WebGL';
            var debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
            return {
                vendor: gl.getParameter(debugInfo ? debugInfo.UNMASKED_VENDOR_WEBGL : gl.VENDOR),
                renderer: gl.getParameter(debugInfo ? debugInfo.UNMASKED_RENDERER_WEBGL : gl.RENDERER)
            };
        } catch(e) {
            return String(e);
        }
    """)
    print(f"WebGL Info: {webgl_info}")

    # Check chrome runtime object
    chrome_obj = driver.execute_script("return Boolean(window.chrome && window.chrome.csi && window.chrome.loadTimes && window.chrome.app)")
    print(f"Chrome runtime completeness: {chrome_obj}")

    # Check function toString integrity
    tostring_check = driver.execute_script("return Function.prototype.toString.call(WebGLRenderingContext.prototype.getParameter)")
    print(f"getParameter toString: {tostring_check}")

    # Second check: Incolumitas webdriver test
    print("\nNavigating to https://incolumitas.com/pages/webdriver/ ...")
    driver.get("https://incolumitas.com/pages/webdriver/")
    time.sleep(8)
    driver.save_screenshot("/app/data-dir/bot_detection_incolumitas.png")
    print("Incolumitas screenshot saved to /app/data-dir/bot_detection_incolumitas.png")

    try:
        incolumitas_result = driver.execute_script("return document.body ? document.body.innerText : ''")
        for line in (incolumitas_result or "").splitlines():
            line_str = line.strip()
            if any(k in line_str.lower() for k in ("is webdriver present", "webdriver", "bot detected", "headless", "phantom", "selenium")):
                print(f"Incolumitas check: {line_str}")
    except Exception as exc:
        print(f"Incolumitas text check: {exc}")

finally:
    driver.quit()
    print("\nDriver cleanly stopped.")
