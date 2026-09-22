# iOS Device Testing Guide

Reference guide for automated and manual testing on the connected iOS device.

---

## 1. Device Hardware & Network Coordinates

- **Device**: iPhone 14 Pro (`iPhone15,2`)
- **OS Version**: iOS 16.1.2 (rootless jailbreak ready)
- **Viewport**: 393 × 852 pt (Scale: 3x, Resolution: 1179 × 2556 px)
- **Host Workstation LAN IP**: `192.168.1.109`
- **Backend Service URL**: `http://192.168.1.109:8000`
- **Mobile Web App URL**: `http://192.168.1.109:8000/mobile`
- **Desktop Studio URL**: `http://192.168.1.109:8000/`

---

## 2. Available `ios-mcp` Tools

The environment exposes 46 iOS automation tools under the `ios-mcp` server:
- **Device Lifecycle & Info**: `get_device_info`, `run_command`, `get_battery`, `get_brightness`, `set_brightness`, `get_volume`, `set_volume`
- **App Control**: `launch_app`, `kill_app`, `list_apps`, `list_running_apps`, `get_frontmost_app`, `open_url`
- **Hardware Buttons & Gestures**: `press_home`, `wake_and_home`, `press_power`, `tap_screen`, `tap_element`, `swipe_screen`, `drag_and_drop`, `double_tap`, `long_press`
- **Visual Inspection**: `screenshot`, `ocr_screen`, `describe_screen`, `get_ui_elements`, `get_screen_info`
- **Input**: `input_text`, `type_text`, `press_key`, `get_clipboard`, `set_clipboard`

---

## 3. Step-by-Step Testing Recipes

### Recipe A: Opening Mobile Web App on Safari
```json
{
  "ServerName": "ios-mcp",
  "ToolName": "open_url",
  "Arguments": {
    "url": "http://192.168.1.109:8000/mobile"
  }
}
```

### Recipe B: Visual Verification via Screenshot
```json
{
  "ServerName": "ios-mcp",
  "ToolName": "screenshot",
  "Arguments": {}
}
```

### Recipe C: OCR Text Extraction
```json
{
  "ServerName": "ios-mcp",
  "ToolName": "ocr_screen",
  "Arguments": {}
}
```

### Recipe D: Tap & Navigation Testing
```json
{
  "ServerName": "ios-mcp",
  "ToolName": "tap_screen",
  "Arguments": {
    "x": 196,
    "y": 426
  }
}
```
