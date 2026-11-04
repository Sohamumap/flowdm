# Putting FlowDM on AWS (runs 24/7)

You'll create **one small server** in AWS and paste a setup script into it. The server then
installs everything by itself: Docker, the database, the app, and a free HTTPS certificate.
**No terminal or SSH is needed.**

Time needed: about 20 minutes (most of it is waiting).

| Part | What | Time |
|---|---|---|
| 1 | Get a free web address from DuckDNS | 3 min |
| 2 | Launch the server in AWS (Mumbai region) with the setup script | 7 min |
| 3 | Wait for it to install itself, then log in | 10 min |

**Cost:** new AWS accounts get free credits (currently up to $200 for the first 6 months) that
cover this server. After that, a `t3.micro` in Mumbai costs roughly **$13–14/month** in total
(server about $8, 20 GB disk about $2, public IP address about $3.60). The app itself uses about
200 MB of the server's 1 GB of memory.

---

## Part 1: Free web address (DuckDNS)

Instagram can only send events to an `https://` address, so the server needs a name.

1. Go to **<https://www.duckdns.org>** and sign in (Google, GitHub, etc.).
2. In **sub domain**, type a name, e.g. `mybrandbot`, and click **add domain**.
   Your address is now **`mybrandbot.duckdns.org`**. Leave the IP as it is; the server
   fills it in itself.
3. At the top of the page, copy your **token** (looks like
   `a1b2c3d4-1234-5678-9abc-def012345678`).

Keep the name and token for Part 2.

## Part 2: Launch the server

1. Sign in to the **AWS Console**: <https://console.aws.amazon.com>
2. **Region** (top-right, next to your name): choose **Asia Pacific (Mumbai) ap-south-1**.
3. In the search bar type **EC2** → open it → **Launch instance** (orange button).
4. Fill in the form from top to bottom:

   | Section | What to choose |
   |---|---|
   | **Name and tags** | `flowdm` |
   | **Application and OS Images** | **Ubuntu** → *Ubuntu Server 24.04 LTS (HVM), SSD Volume Type*, architecture **64-bit (x86)** |
   | **Instance type** | **t3.micro** (shows "Free tier eligible") |
   | **Key pair (login)** | **Proceed without a key pair**. You don't need one; the browser-based "EC2 Instance Connect" works without it. |
   | **Network settings** | Keep "Create security group" and tick all three: **Allow SSH traffic from Anywhere**, **Allow HTTPS traffic from the internet**, **Allow HTTP traffic from the internet** |
   | **Configure storage** | **20** GiB, **gp3** |

   *SSH is only used if you ever open the browser terminal (EC2 Instance Connect). Ubuntu
   only accepts short-lived keys, never passwords.*

5. Open **Advanced details** (at the bottom), scroll all the way down to **User data**.
6. Open [`deploy/aws-user-data.sh`](../deploy/aws-user-data.sh) on GitHub, click **Copy raw file**,
   and paste it into the User data box. Then edit the four lines near the top:

   ```bash
   DUCKDNS_SUBDOMAIN="mybrandbot"                          # your name from Part 1, without .duckdns.org
   DUCKDNS_TOKEN="a1b2c3d4-1234-5678-9abc-def012345678"    # your token from Part 1
   ADMIN_EMAIL="you@gmail.com"                             # your dashboard login
   ADMIN_PASSWORD="SomethingStrong123"                     # 8+ characters, no quotes or $
   ```

7. Click **Launch instance** (right side). Then **View all instances**.

## Part 3: Wait, then log in

The server now installs itself. That takes **about 10 minutes**.

* **To watch progress:** select the instance → **Actions → Monitor and troubleshoot → Get system
  log**. Lines starting with `FlowDM:` show each step. The last one says
  `FlowDM: DONE. Open https://mybrandbot.duckdns.org ...`
  The log only refreshes every few minutes, so be patient.
* Then open **https://mybrandbot.duckdns.org** (your name) and log in with the email and password
  you put in the script. **Change the password** in Settings afterwards.

If the log shows `FlowDM: SETUP FAILED: ...`, it says what to fix (usually a typo in the
DuckDNS name or token). Terminate the instance (**Instance state → Terminate**) and launch a new
one with the corrected script.

**Next:** connect Instagram by following [META_SETUP.md](META_SETUP.md). You paste the Meta
app's ID and secret into **Settings → Instagram app** in the dashboard; no server files needed.

---

## It runs 24/7

* The app restarts itself after crashes and server reboots.
* If the instance is stopped and started, AWS gives it a new IP address. The server updates
  DuckDNS every 5 minutes, so the address keeps working. (An Elastic IP isn't needed.)
* The database is backed up every night to `/opt/flowdm/backups` (last 14 days kept).
* Ubuntu installs security updates automatically.
* Instagram access tokens are renewed automatically by the app.

## Updating to a newer version

When new code is pushed to GitHub:

1. EC2 → select the instance → **Connect** → **EC2 Instance Connect** → **Connect**.
   A terminal opens in your browser.
2. Run:

   ```bash
   sudo /opt/flowdm/deploy/update.sh
   ```

   It downloads the new code, rebuilds, restarts, and updates the database automatically.

## Useful commands (in the EC2 Instance Connect browser terminal)

| What | Command |
|---|---|
| Is everything running? | `cd /opt/flowdm && sudo docker compose ps` |
| Live logs of the worker | `cd /opt/flowdm && sudo docker compose logs -f worker` (Ctrl+C to stop) |
| Restart everything | `cd /opt/flowdm && sudo docker compose restart` |
| Back up now | `sudo /opt/flowdm/deploy/backup.sh` |
| Setup log | `sudo cat /var/log/cloud-init-output.log` |

## Troubleshooting

* **The site doesn't open after 15 minutes**: check *Get system log* for `FlowDM:` lines.
  Make sure the security group allows HTTP (80) and HTTPS (443) from anywhere.
* **Browser says the certificate is invalid**: wait a few more minutes. Caddy requests the
  free certificate as soon as DuckDNS points at the server. Check the DuckDNS page shows the
  instance's *Public IPv4 address*.
* **Meta can't verify the webhook URL**: open `https://<your-name>.duckdns.org/api/health` in
  a browser; it must show `{"ok":true}`. Copy the verify token from **Settings → Webhook setup**.
* **Comments don't trigger anything**: see **Activity → Webhook log** in the dashboard, and
  [META_SETUP.md §8](META_SETUP.md#8-going-live-so-anyone-not-just-testers-triggers-your-automations).

---

## Manual setup (alternative, for people comfortable with SSH)

Launch the same instance *without* User data, then on the server:

```bash
git clone https://github.com/Sohamumap/flowdm.git flowdm && cd flowdm
sudo ./deploy/setup-ec2.sh                 # swap, Docker, security updates; then log out and in
./deploy/init-env.sh mybrandbot.duckdns.org you@example.com   # prints your dashboard password
nano .env                                   # optional: DUCKDNS_TOKEN for automatic IP updates
docker compose up -d --build
```
