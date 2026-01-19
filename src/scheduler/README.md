# Scheduler Module

## Features
- Schedule engine (cron-like)
- 3 modes: Home / Away / Sleep
- Auto/manual mode switching
- Per-rule scheduling
- Holiday calendar

## Modes
| Mode | Description |
|------|-------------|
| Home | Normal monitoring, family notifications |
| Away | Full security, stranger alerts |
| Sleep | Night mode, reduced sensitivity |

## Configuration
```yaml
scheduler:
  default_mode: home
  auto_switch: true
  schedules:
    - mode: away
      cron: "0 8 * * 1-5"  # Weekdays 8AM
    - mode: home
      cron: "0 18 * * 1-5" # Weekdays 6PM
    - mode: sleep
      cron: "0 22 * * *"   # Every day 10PM
```

## TODO
- [ ] Cron parser
- [ ] Mode state machine
- [ ] Holiday calendar integration
- [ ] Rule-based scheduling
