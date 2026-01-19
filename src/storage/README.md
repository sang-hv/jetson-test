# Storage Module

## Features
- Local storage management
- Auto cleanup
- Circular buffer
- Media indexing (SQLite)
- Export function
- Disk space alert

## Storage Layout
```
/data/
├── db/
│   └── local.db      # SQLite database
├── media/
│   ├── snapshots/    # Event snapshots
│   └── clips/        # Video clips
├── faces/
│   └── embeddings/   # Face embeddings cache
└── logs/
    └── app.log       # Application logs
```

## TODO
- [ ] SQLite schema design
- [ ] Circular buffer implementation
- [ ] Auto cleanup scheduler
- [ ] Disk space monitoring
- [ ] Export API
