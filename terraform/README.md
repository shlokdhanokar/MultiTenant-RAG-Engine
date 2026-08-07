# Oracle Cloud RAG Engine Deployment

Fully automated Terraform + Bash deployment to Oracle Cloud Always Free tier.

## Files

- **`main.tf`** — Terraform infrastructure code (VCN, subnet, instance)
- **`terraform.tfvars`** — Your credentials (FILL THIS IN)
- **`deploy.sh`** — Runs on instance to install RAG engine

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
# Download script
curl -O https://raw.githubusercontent.com/shlokdhanokar/MultiTenant-RAG-Engine/main/terraform/deploy.sh
chmod +x deploy.sh
./deploy.sh
```

When prompted, edit `.env` with:
- `MONGODB_URI` — Your MongoDB Atlas connection string
- `GEMINI_API_KEY` — From [aistudio.google.com](https://aistudio.google.com)
- `APP_BASE_URL` — Your public IP or domain

### 6. Get SSL Certificate (Optional)

```bash
ssh -i ~/.ssh/id_rsa ubuntu@<public-ip>
sudo certbot --nginx -d your-domain.com
```

### 7. Test

```bash
curl http://<public-ip>/health
# Should return: {"status":"ok"}
```

## Cleanup

```bash
terraform destroy
# Type "yes" to confirm
```

## Cost

**✅ 100% FREE** under Oracle Always Free tier:
- 2x Ampere A1 compute instances (4 cores, 24GB RAM)
- No cold starts, always-on
- Keep account active (login every 30 days)

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

---

For RAG engine docs, see [../README.md](../README.md)
