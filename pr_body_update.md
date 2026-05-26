## Summary
This PR enables real-time push notifications for the IntruderWatch mobile applications and updates the project documentation to reflect the new capabilities.

## Key Changes
- **Cloudflare Tunnel Integration**: Added a `tunnel` service using `cloudflared` to securely expose `ntfy` and `viewer_service` to the internet via a public domain.
- **Real-Time Push**: Configured `ntfy` to use the official upstream push server, enabling instant delivery to iOS and Android devices without manual refreshes.
- **Secure Public URLs**: Updated `alert_service` to use the secure public-facing domain for image attachments, ensuring accessibility over mobile data (5G/LTE).
- **Environment Driven**: Moved public domain configuration to environment variables for better security and flexibility.
- **Documentation Update**: Refactored the `README.md` to include sections on the Unified Alerting Stack, Secure Remote Access, and Proactive Monitoring thresholds.

## Verification
- Verified instant push notification arrival on mobile devices.
- Confirmed working image attachments over HTTPS using the Cloudflare Tunnel.
- Verified README rendering and accuracy.
