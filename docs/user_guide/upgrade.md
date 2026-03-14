# Upgrade and Migration Guide

This guide covers upgrading to **Docker Wyze Bridge V4.0** from earlier versions or from other forks.

## Upgrading from Upstream (idisposable/docker-wyze-bridge)

The V4.0 release is a major update focused on Wyze Cam V4 support, improved Home Assistant integration, and a more stable streaming backend.

### What has Changed?
- **New Home Assistant Slug:** The add-on now uses `docker_wyze_bridge_v4`. This allows it to install alongside the original upstream add-on if needed.
- **KVS/WebRTC Backend:** Support for Wyze Cam V4 and improved latency for V3/V3 Pro cameras.
- **Optimized Ports:** Defaults have changed for Home Assistant users (e.g., `58554` for RTSP) to avoid common port conflicts.
- **Polished Web UI:** New one-click copy buttons for stream URLs and improved status reporting.

### Migration Steps
1.  **Backup your configuration:** If you are using the Home Assistant add-on, copy your current Wyze credentials and `CAM_OPTIONS` from the configuration tab.
2.  **Stop the old add-on:** If you were using the upstream version, stop it before installing the V4.0 release to avoid port conflicts.
3.  **Add the new repository:** Follow the [Home Assistant Install Guide](./install_ha.md) to add the `thatdaveguy1/docker-wyze-bridge` repository.
4.  **Install V4.0:** Install the new **Docker Wyze Bridge (V4.0)** add-on and paste your saved configuration into the configuration tab.
5.  **Update your stream links:** If you have hardcoded RTSP or HLS links in your dashboards or Frigate, update them to use the new ports (`58554`, `58888`, `58889`).

## Upgrading from Local Patched Build

If you were already using the **Docker Wyze Bridge (Local Patched)** add-on from this repo, the transition to V4.0 is seamless.

### Migration Steps
1.  The `local_docker_wyze_bridge_local` slug is being replaced by the official `docker_wyze_bridge_v4` slug.
2.  Your existing configuration can be copied over directly.
3.  The V4.0 release includes the latest stability fixes and the final polished Web UI.

## Troubleshooting Upgrades

### "Port already in use"
- **Problem:** The V4.0 add-on fails to start with a port conflict error.
- **Solution:** 
  1.  Ensure you have stopped any other versions of the bridge or other services using ports `8554`, `8888`, `8889`, or `5000`.
  2.  If you are in Home Assistant, check the **Configuration** tab and change the port mapping to an unused port block.

### "Cameras not connecting after upgrade"
- **Problem:** After upgrading, some cameras stay offline or fail to connect.
- **Solution:** 
  1.  Check the logs for `Wyze API Pull Error`. You may need to refresh your API credentials or clear the bridge's local cache.
  2.  Verify `ENABLE_V4_KVS_TRIAL` and `ENABLE_ALL_RTC_TRIAL` are set to `true` (default in V4.0).
  3.  Restart the bridge to clear any stale session data.
