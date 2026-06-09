#!/bin/bash
# Bring up wlan0's wpa_supplicant and KEEP it running regardless of whether any
# saved network is currently in range. The web panel relies on the control
# socket being reachable in order to switch networks, so a missing AP must never
# take the supplicant down. (Previously this script blocked in a foreground
# association loop; when no saved AP was nearby it never exited, systemd hit its
# start timeout and SIGTERM'd the whole cgroup — killing the supplicant too.)
killall wpa_supplicant 2>/dev/null
rm -f /var/run/wpa_supplicant/wlan0
sleep 1

iw reg set GB
ip link set wlan0 up
sleep 2

# The only step that must succeed: daemonize the supplicant. ctrl_interface in
# the conf (DIR=/var/run/wpa_supplicant) is what creates the socket wpa_cli/the
# panel need.
wpa_supplicant -B -D nl80211 -i wlan0 \
  -c /etc/wpa_supplicant/wpa_supplicant-wlan0.conf \
  -P /var/run/wpa_supplicant.pid

# Best-effort, in the background: if a saved network associates within ~60s,
# grab a DHCP lease (dhclient also installs the default route from the lease, so
# we no longer hardcode a gateway). Backgrounding lets this script exit 0
# immediately, so systemd start completes and can never time out / kill the
# daemon when nothing is in range — the panel can then switch us manually.
(
  for i in $(seq 1 12); do
    if [ "$(wpa_cli -i wlan0 status 2>/dev/null | sed -n 's/^wpa_state=//p')" = "COMPLETED" ]; then
      dhclient wlan0
      break
    fi
    sleep 5
  done
) &

exit 0
