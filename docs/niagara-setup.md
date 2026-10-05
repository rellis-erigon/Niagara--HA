# Niagara Station Setup Guide

Step-by-step guide to prepare your Tridium Niagara 4 station for integration with Home Assistant. These steps are performed in **Niagara Workbench** and require no paid modules or licenses.

---

## Prerequisites

- Niagara 4 station running and accessible via Workbench
- Admin-level access to the station
- Network connectivity between the Niagara station and your Home Assistant instance

---

## Step 1 — Enable HTTPS

oBIX requires HTTPS on the Niagara station.

1. Open **Niagara Workbench** and connect to your station
2. Navigate to **Station → Services → WebService**
3. Open the **AX Property Sheet** view
4. Set **HTTPS Enabled** to `true`
5. Set the **HTTPS Port** (default `443`)
6. Save and commit the changes

> **Note:** If your station already serves HTTPS (check by browsing to `https://<station-ip>:<port>/`), you can skip this step.

---

## Step 2 — Install the oBIX Network Driver

The oBIX network driver exposes points over the oBIX REST protocol.

1. Navigate to **Station → Config → Drivers**
2. Click **New** in the toolbar
3. Set **Type to Add** = `Obix Network`
4. Click **OK**
5. The oBIX network driver appears under Drivers

> **Tip:** If `Obix Network` does not appear in the list, you may need to install the `obix` module from the Niagara module repository.

---

## Step 3 — Add HTTPBasicScheme Authentication

oBIX requires a specific authentication scheme that is not enabled by default.

1. Navigate to **Station → Services → AuthenticationService → AuthenticationSchemes**
2. Open the **Palette** panel (sidebar)
3. Browse to **baja → AuthenticationSchemes → WebServicesSchemes → HTTPBasicScheme**
4. Drag and drop (or click **Add**) to add it to the AuthenticationSchemes list
5. Save changes

---

## Step 4 — Create an oBIX User Account

Create a dedicated user account for the Home Assistant integration.

1. Navigate to **Station → Services → UserService**
2. Right-click and select **Duplicate** on the Admin user (or create a new user)
3. Rename the new user to something descriptive (e.g., `obixUser`, `ha_reader`)
4. Open the new user's **AX Property Sheet**
5. Set a **strong password**
6. Change **AuthenticationSchemeName** to `HTTPBasicScheme`
7. Save changes

> **Security:** Create a read-only user if you only need monitoring. This limits what the integration can access on your BMS.

Use this username and password when configuring the integration or add-on in Home Assistant.

---

## Step 5 — Verify oBIX Access (Optional)

Test that oBIX is working before configuring Home Assistant.

1. Open a web browser
2. Navigate to `https://<station-ip>:<port>/obix/about/`
3. Enter the oBIX user credentials when prompted
4. You should see an XML response with station information

If you see an XML document, oBIX is ready. If you get a connection error or authentication failure, review the steps above.

### Test Point Discovery

Navigate to `https://<station-ip>:<port>/obix/config/` to see the top-level oBIX tree. Points discovered by the integration come from walking this tree.

---

## Exporting Points (If Needed)

On some Niagara configurations, points must be explicitly exported before oBIX can see them. This is typically needed when points are under custom drivers rather than the standard Niagara network.

1. Navigate to the points you want to expose (e.g., under **Drivers → NiagaraNetwork → {device} → points**)
2. Right-click on each point (or a folder of points) → **Actions → Export**
3. Select **Obix Export** as the export type
4. Repeat for all points/folders you want available in Home Assistant

> **Tip:** Export an entire folder to expose all points under it at once. Only exported points will be discoverable.

Most modern Niagara 4 stations expose all points under `/config/` automatically. The integration discovers everything in that tree.

---

## Firewall and Network Notes

| Requirement | Details |
|---|---|
| **Protocol** | HTTPS (TCP) |
| **Default port** | 443 |
| **Direction** | Home Assistant → Niagara station (outbound from HA) |
| **Authentication** | HTTP Basic Auth over TLS |
| **Certificate** | Self-signed is fine (set "Verify SSL" to false in HA) |

Ensure your firewall allows Home Assistant to reach the Niagara station on the configured HTTPS port. No inbound connections to Home Assistant are needed.

---

## Next Steps

- [Native Integration Guide](integration-guide.md) — set up the HA custom integration
- [MQTT Add-on Guide](addon-guide.md) — set up the MQTT-based add-on
