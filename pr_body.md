## Summary of Changes
- **Thermal Efficiency**: Switched \`human_detector\` from YOLO11 Large to Medium and implemented MSE-based motion filtering in \`frame_capturer\`. This reduced GPU Junction Temp from ~97°C to ~55-65°C and cut power draw by ~100W+.
- **Resource Optimization**: Standardized capture rate to 4 FPS and reduced human detector replicas from 4 to 2 for improved VRAM headroom.
- **Observability**: Added real-time GPU junction temperature and power draw gauges to Grafana.
- **Dashboard Clarity**: Replaced the service status table with a visual **State Timeline** and optimized panel height for clear visibility of all 8+ camera rows.
- **Configuration**: Added \`MOTION_THRESHOLD\` to \`.env.example\` to allow tuning of motion sensitivity.
- **Documentation**: Updated \`README.md\` and \`ARCHITECTURE.md\` (including Mermaid diagrams) to reflect the new high-efficiency system state.

## Verification Performed
- Verified GPU temperature and power draw drop via live Prometheus metrics.
- Confirmed motion filtering efficiency (~56% frames skipped during night hours).
- Verified Grafana dashboard rendering and service health states.
