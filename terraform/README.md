# Oracle Cloud RAG Engine Deployment

Fully automated Terraform + Bash deployment to Oracle Cloud Always Free tier.

## Files

- **`main.tf`** — the whole stack: VCN, subnet, internet gateway, route table,
  security list, and one compute instance
- **`terraform.tfvars`** — your credentials (FILL THIS IN). Gitignored, along
  with `*.tfstate*` — Terraform writes the values of sensitive variables into
  state in plaintext
- **`deploy.sh`** — runs on the instance and installs everything: service,
  nginx, UI bundle, TLS, dynamic DNS
- **`retry-apply.ps1`** — retries `terraform apply` on "Out of host capacity".
  Only useful if you switch the shape to `VM.Standard.A1.Flex` (see Cost below)

## Prerequisites

1. **Oracle Cloud Account** (free) — [oracle.com/cloud/free](https://oracle.com/cloud/free)
2. **Terraform** — [terraform.io/downloads](https://terraform.io/downloads)
3. **Oracle CLI API key** — Setup at [cloud.oracle.com](https://cloud.oracle.com)
4. **SSH key pair** — `ssh-keygen -t rsa -b 4096` (if you don't have one)

## Quick Start

### 1. Get Oracle Credentials

In Oracle Cloud Console:
- Click your **user icon** → **User Settings**
- **API Keys** → **Add API Key** → Download private key as `~/.oci/oci_api_key.pem`
- Copy: `tenancy_ocid`, `user_ocid`, `fingerprint`, `compartment_ocid`

### 2. Edit `terraform.tfvars`

Open and fill in your credentials:

```hcl
tenancy_ocid     = "ocid1.tenancy.oc1..xxxxx"
user_ocid        = "ocid1.user.oc1..xxxxx"
fingerprint      = "aa:bb:cc:dd:ee:ff"
private_key_path = "~/.oci/oci_api_key.pem"
public_key_path  = "~/.ssh/id_rsa.pub"
compartment_ocid = "ocid1.compartment.oc1..xxxxx"
region           = "ap-mumbai-1"
```

### 3. Deploy Infrastructure

```bash
cd terraform
terraform init
terraform plan
terraform apply
# Type "yes" to confirm
```

Takes ~2 minutes. You'll get the **public IP** in the output.

### 4. SSH Into Instance

```bash
ssh -i ~/.ssh/id_rsa ubuntu@<public-ip-from-terraform-output>
```

### 5. Run Deployment Script

```bash
curl -O https://raw.githubusercontent.com/shlokdhanokar/MultiTenant-RAG-Engine/main/terraform/deploy.sh
chmod +x deploy.sh
./deploy.sh
```

That installs Python 3.11 and Node, builds the UI bundle, and leaves nginx
serving the SPA on port 80 with the API paths proxied to gunicorn. When
prompted, edit `.env` with:

- `MONGODB_URI` — Your MongoDB Atlas connection string
- `GEMINI_API_KEY` — From [aistudio.google.com](https://aistudio.google.com)

`APP_BASE_URL` is written for you and kept in step with whatever the box is
reachable as.

### 6. HTTPS and a hostname (optional)

Oracle hands out an *ephemeral* public IP that can change across a stop/start,
which breaks both DNS and any certificate bound to it. Pass a domain and the
script handles the whole chain — dynamic DNS, certificate, redirect, renewal:

```bash
DOMAIN=your-name.duckdns.org \
CERTBOT_EMAIL=you@example.com \
DUCKDNS_TOKEN=<token from duckdns.org> \
./deploy.sh
```

The DuckDNS token is written to `/etc/duckdns.token` (mode 600) and refreshed
by a cron entry every five minutes; certificates renew through the
`certbot.timer` the package installs. Any other DNS provider works too — point
an A record at the instance and omit `DUCKDNS_TOKEN`.

> Dynamic-DNS domains are blocked wholesale by some corporate and campus
> network filters, so a `*.duckdns.org` name can be unreachable on exactly the
> networks you demo on. [DEPLOY.md](../DEPLOY.md#3-putting-vercel-in-front-of-a-self-hosted-origin)
> covers putting Vercel in front of this box to sidestep that.

### 7. Test

```bash
curl http://<public-ip>/health      # {"status":"ok"}
curl http://<public-ip>/            # the UI
```

## Cleanup

```bash
terraform destroy
# Type "yes" to confirm
```

## Cost and shape

**100% free** under Oracle's Always Free tier, with no cold starts. Keep the
account active by logging in every 30 days.

`main.tf` provisions **`VM.Standard.E2.1.Micro`** — x86, 1 OCPU, 1 GB RAM.

Always Free also covers `VM.Standard.A1.Flex` (Ampere ARM, up to 4 OCPU and
24 GB across two instances), which is vastly better hardware. It is also
perpetually capacity-starved: `terraform apply` fails with **"Out of host
capacity"** in most regions, indefinitely, and no amount of retrying is
guaranteed to land one. E2.1.Micro provisions immediately, every time, which is
why it is the default here.

To try A1 anyway, change `shape` in **both** places — the instance *and* the
`oci_core_images` data source. Filtering images by A1.Flex returns aarch64
builds; leaving the data source on E2 hands an x86 image to an ARM instance,
which simply will not boot. A1 also takes a `shape_config` block for OCPU and
memory, which the fixed E2 shape does not. Then run `retry-apply.ps1`.

1 GB is tight, and `deploy.sh` compensates: 4 GB of swap (`pip install`, OCR and
the Vite build each exceed 1 GB on their own), `vm.swappiness=10` so it does not
thrash, and two Gunicorn workers recycled every ~200 requests.

## Troubleshooting

**Can't SSH?**
```bash
terraform show  # Verify instance IP
```

**RAG not responding?**
```bash
ssh -i ~/.ssh/id_rsa ubuntu@<ip>
sudo systemctl status rag-engine
sudo journalctl -u rag-engine -n 50
```

**Terraform error?**
```bash
terraform init -upgrade
terraform plan
```

**"Out of host capacity" on apply?** You are on an A1 shape. See
[Cost and shape](#cost-and-shape).

**Reachable on port 22 but not 80/443?** Oracle's Ubuntu images ship iptables
REJECT rules that block those ports even when the VCN security list allows
them — opening the security list alone is not enough. This is the single most
common cause, and `deploy.sh` handles it; if you deployed by hand:

```bash
sudo iptables -L INPUT -n --line-numbers        # find the REJECT line number
sudo iptables -I INPUT <n> -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT <n> -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

The rules must go **before** the catch-all REJECT — iptables matches top-down
and stops at the first hit, so inserting after it silently does nothing.

**Site broke after a reboot?** The public IP is ephemeral and may have changed.
Check `/var/log/duckdns.log`, or re-run `/usr/local/bin/duckdns-update.sh`.

---

For RAG engine docs, see [../README.md](../README.md)
