variable "juju_model" {
  description = "Juju model uuid"
  type        = string
  default     = null
}

variable "charm_name" {
  description = "Name of the charm on https://charmhub.io"
  type        = string
  default     = "postgresql"
  nullable    = false
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

variable "storage" {
  description = "Storage directive"
  type        = map(string)
  default     = {}
}

variable "config" {
  description = "Application configuration. Details at https://charmhub.io/postgresql/configurations"
  type        = map(string)
  default     = {}
}

variable "enable_expose" {
  description = "Whether to expose the application"
  type        = bool
  default     = true
}

variable "machine" {
  description = "Target Juju machine to deploy on"
  type        = string
  default     = null
}
