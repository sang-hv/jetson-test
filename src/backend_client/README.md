# Backend Client Module

## Features

### Event Management
- Local queue (offline support)
- Deduplication
- Snapshot capture
- Video clip recording
- Priority queue
- Retry mechanism

### Cloud Connection
- WebSocket client
- REST API client
- Event/media upload
- Config sync
- Remote control
- Heartbeat service
- Offline mode

### Face Database
- Face enrollment
- Cloud sync
- Local cache
- Whitelist/blacklist management
- CRUD operations

## Configuration
```yaml
backend:
  api_url: https://api.example.com
  ws_url: wss://ws.example.com
  api_key: ${API_KEY}
  timeout: 30
  retry_count: 3
```

## TODO
- [ ] WebSocket client
- [ ] REST API client
- [ ] Event queue system
- [ ] Face database sync
- [ ] Offline mode handling
