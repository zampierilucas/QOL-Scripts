"""Probe screen_brightness_control for both displays: which backend each
monitor is using, current brightness, and a one-shot set attempt."""
import logging
import screen_brightness_control as sbc

logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")

print("== list_monitors_info ==")
for m in sbc.list_monitors_info():
    print(m)

print("\n== get_brightness ==")
print(sbc.get_brightness())

print("\n== set_brightness(100, display=0) ==")
try:
    sbc.set_brightness(100, display=0)
    print("  ok")
except Exception as e:
    print(f"  FAIL: {e}")

print("\n== set_brightness(100, display=1) ==")
try:
    sbc.set_brightness(100, display=1)
    print("  ok")
except Exception as e:
    print(f"  FAIL: {e}")

print("\n== get_brightness AFTER ==")
print(sbc.get_brightness())
