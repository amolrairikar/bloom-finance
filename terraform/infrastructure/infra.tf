terraform {
  backend "gcs" {
    bucket  = "amol-personal-finance-app-infrastructure"
    prefix  = "terraform/state"
  }
}

provider "google" {
  project = var.project_id
  region  = "us-central1"
}

resource "google_project_service" "firestore" {
  project = var.project_id
  service = "firestore.googleapis.com"
}

resource "google_firestore_database" "application_database" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.firestore_db_location
  type        = "FIRESTORE_NATIVE"
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_ENABLED"
  delete_protection_state = "DELETE_PROTECTION_ENABLED"
}