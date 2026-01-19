# Camera Module

## Components

### rtsp/
RTSP camera client.
- Multi-camera connection
- Frame capture pipeline
- Health check & auto-reconnect
- Local recording (fallback)

### streaming/
Live video streaming.
- WebRTC server
- HLS streaming
- Adaptive bitrate
- Multi-viewer support
- Stream authentication

## Configuration
```yaml
cameras:
  - id: cam_01
    name: Front Door
    rtsp_url: rtsp://192.168.1.100:554/stream1
    enabled: true
    
  - id: cam_02
    name: Backyard
    rtsp_url: rtsp://192.168.1.101:554/stream1
    enabled: true
```

## TODO
- [ ] RTSP client with OpenCV
- [ ] Connection pool management
- [ ] Health check mechanism
- [ ] WebRTC implementation
- [ ] HLS transcoding
