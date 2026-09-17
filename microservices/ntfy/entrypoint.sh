#!/bin/sh
set -e

AUTH_FILE="${NTFY_AUTH_FILE:-/var/cache/ntfy/auth.db}"

if [ -n "$AUTH_FILE" ]; then
    mkdir -p "$(dirname "$AUTH_FILE")"
    if [ ! -f "$AUTH_FILE" ]; then
        touch "$AUTH_FILE"
        chmod 600 "$AUTH_FILE" 2>/dev/null || true
    fi

    provision_user() {
        username="$1"
        password="$2"
        role="$3"

        if [ -z "$username" ] || [ -z "$password" ]; then
            return 0
        fi

        if ! ntfy user list 2>/dev/null | grep -q "^user $username "; then
            echo "[ntfy-init] Provisioning user '$username' (role: $role)..."
            NTFY_PASSWORD="$password" ntfy user add --role="$role" "$username"
        else
            echo "[ntfy-init] Syncing credentials for user '$username'..."
            NTFY_PASSWORD="$password" ntfy user change-pass "$username"
            ntfy user change-role "$username" "$role" 2>/dev/null || true
        fi
    }

    # Provision Admin User (for phone app and web management)
    if [ -n "$NTFY_ADMIN_USER" ] && [ -n "$NTFY_ADMIN_PASSWORD" ]; then
        provision_user "$NTFY_ADMIN_USER" "$NTFY_ADMIN_PASSWORD" "admin"
    fi

    # Provision Alert Service User (least-privilege write-only for internal alerts)
    if [ -n "$NTFY_ALERT_SERVICE_USER" ] && [ -n "$NTFY_ALERT_SERVICE_PASSWORD" ]; then
        provision_user "$NTFY_ALERT_SERVICE_USER" "$NTFY_ALERT_SERVICE_PASSWORD" "user"
        ntfy access "$NTFY_ALERT_SERVICE_USER" "*" write-only
    fi
fi

echo "[ntfy-init] Initialization complete. Starting ntfy server..."
exec ntfy "$@"
