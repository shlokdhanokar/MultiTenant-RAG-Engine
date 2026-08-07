terraform {
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 5.0"
    }
  }
}

provider "oci" {
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
  region           = var.region
}

variable "tenancy_ocid" {
  type = string
}

variable "user_ocid" {
  type = string
}

variable "fingerprint" {
  type = string
}

variable "private_key_path" {
  type = string
}

variable "region" {
  type    = string
  default = "ap-mumbai-1"
}

variable "compartment_ocid" {
  type = string
}

# Create VCN
resource "oci_core_vcn" "rag_vcn" {
  compartment_id = var.compartment_ocid
  display_name   = "rag-engine-vcn"
  cidr_block     = "10.0.0.0/16"
}

# Create public subnet
resource "oci_core_subnet" "rag_subnet" {
  compartment_id      = var.compartment_ocid
  vcn_id              = oci_core_vcn.rag_vcn.id
  display_name        = "rag-engine-subnet"
  cidr_block          = "10.0.0.0/24"
  route_table_id      = oci_core_route_table.rag_route_table.id
  security_list_ids   = [oci_core_security_list.rag_security_list.id]
  prohibit_internet_ingress  = false
  prohibit_public_ip_on_vnic = false
}

# Internet Gateway
resource "oci_core_internet_gateway" "rag_igw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.rag_vcn.id
  display_name   = "rag-engine-igw"
  enabled        = true
}

# Route Table
resource "oci_core_route_table" "rag_route_table" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.rag_vcn.id
  display_name   = "rag-engine-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.rag_igw.id
  }
}

# Security List (Firewall)
resource "oci_core_security_list" "rag_security_list" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.rag_vcn.id
  display_name   = "rag-engine-sl"

  # Allow SSH
  ingress_security_rules {
    protocol    = "6"
    source      = "0.0.0.0/0"
    tcp_options {
      min = 22
      max = 22
    }
  }

  # Allow HTTP
  ingress_security_rules {
    protocol    = "6"
    source      = "0.0.0.0/0"
    tcp_options {
      min = 80
      max = 80
    }
  }

  # Allow HTTPS
  ingress_security_rules {
    protocol    = "6"
    source      = "0.0.0.0/0"
    tcp_options {
      min = 443
      max = 443
    }
  }

  # Allow all outbound
  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
  }
}

# Compute Instance
resource "oci_core_instance" "rag_instance" {
  compartment_id      = var.compartment_ocid
  display_name        = "rag-engine-instance"
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[0].name
  shape               = "VM.Standard.A1.Flex"

  shape_config {
    ocpus         = 2
    memory_in_gbs = 12
  }

  create_vnic_details {
    subnet_id              = oci_core_subnet.rag_subnet.id
    display_name           = "rag-engine-vnic"
    assign_public_ip       = true
    assign_private_dns_record = true
  }

  source_details {
    source_type             = "IMAGE"
    source_id               = data.oci_core_images.ubuntu.images[0].id
    boot_volume_size_in_gbs = 50
  }

  metadata = {
    ssh_authorized_keys = file(var.public_key_path)
  }
}

# Get availability domains
data "oci_identity_availability_domains" "ads" {
  compartment_id = var.compartment_ocid
}

# Get Ubuntu 22.04 image
data "oci_core_images" "ubuntu" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "22.04"
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
}

variable "public_key_path" {
  type = string
}

# Output the public IP
output "instance_public_ip" {
  value       = oci_core_instance.rag_instance.public_ip
  description = "Public IP of the RAG engine instance"
}

output "instance_id" {
  value       = oci_core_instance.rag_instance.id
  description = "Instance OCID"
}
