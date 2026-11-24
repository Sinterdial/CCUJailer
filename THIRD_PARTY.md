# Third-party Software and Data Notices

## Upstream Derivation: Pledge Watch (`DieLaughing/pledgewatch`)

**CCUJailer** is a derivative work based on [DieLaughing/pledgewatch](https://github.com/DieLaughing/pledgewatch), licensed under **AGPL-3.0-or-later** (see [LICENSE](LICENSE)).

The RSI public pledge store GraphQL queries, guest `Rsi-Token` cookie handshake, upgrade store (`initShipUpgrade` / `filterShips`) tracking, upgrade route planner algorithm (`UpgradePlannerView.vue`), and minimalist storefront layout CSS were refactored and adapted from Pledge Watch (`pw/store.py`, `pw/upgrades.py`, `pw/catalog.py`, `pw/release.py`, `pw/ingame.py`, `pw/cargo.py`, `pw/site.py`, `web/src/app.jsx`, `web/src/plan.jsx`, `web/src/style.css`).

## External Data Sources

- **Roberts Space Industries (`https://robertsspaceindustries.com`)**: Ship Matrix metadata, standalone pledge availability, Ship Upgrades (CCU), and official Loaner Ship Matrix (`support.robertsspaceindustries.com`). CCUJailer is an unofficial fan tool and is not affiliated with Cloud Imperium Games or Roberts Space Industries.
- **Star Citizen Wiki (`https://starcitizen.tools` & `https://api.star-citizen.wiki`)**: Ship release patch, pledge availability notes, Gold Standard pipeline status, insurance claim/expedite timers, stock weapon loadouts, and pledge price history — used under **CC BY-SA 4.0** (`https://creativecommons.org/licenses/by-sa/4.0/`).
- **UEX (`https://uexcorp.space`)**: In-game vehicle purchase and rental terminal prices in aUEC.
- **sc-cargo.space (`https://github.com/sccargospace/sc-cargo.space`)**: Top-down ship cargo grid dimensions and 3D viewer links — used under the **MIT License**.

## Community Ship Localization

Built-in Chinese ship names (`backend/cj/resources/ship_i18n_builtin.json`) follow community Star Citizen localization conventions, and administrators can configure external `global.ini` or JSON dictionary sources or override individual entries in System Settings.
