provider "google" {
  project = var.project_id
  region  = "us-central1"
}

resource "google_storage_bucket" "terraform_state_bucket" {
  name     = "${var.project_id}-infrastructure"
  location = "us-central1"

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 30
    }
  }
}