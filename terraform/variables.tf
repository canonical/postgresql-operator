variable "juju_model_name" {
  description = "Juju model name"
  type        = string
}

variable "app_name" {
  description = "Name of the application in the Juju model."
  type        = string
  default     = "postgresql"
}

variable "channel" {
  description = "Charm channel to use when deploying"
  type        = string
  default     = "16/stable"
}

variable "revision" {
  description = "Revision number to deploy charm"
  type        = number
  default     = null
}

variable "base" {
  description = "Application base"
  type        = string
  default     = "ubuntu@24.04"
}

variable "units" {
  description = "Number of units to deploy"
  type        = number
  default     = 1
}

variable "constraints" {
  description = "Juju constraints to apply for this application."
  type        = string
  default     = "arch=amd64"
}

variable "storage_size" {
  description = "Storage size"
  type        = string
  default     = "10G"
}

variable "config" {
  description = "Application configuration. Details at https://charmhub.io/postgresql/configurations"
  type        = map(string)
  default     = {}
}

variable "enable_expose" {
  type        = bool
  default     = true
  description = "Whether to expose the application"
}
