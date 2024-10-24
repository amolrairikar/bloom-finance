variable "project_id" {
  description = "The GCP project ID where resources will be created."
  type        = string
}

variable "firestore_db_location" {
  description = "The location where the Firestore DB will be located."
  type        = string
}