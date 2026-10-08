variable "app_name" {
  description = "Name of the application in the Juju model."
  type        = string
  default     = "postgresql"
}

variable "base" {
  description = "Application base"
  type        = string
  default     = "ubuntu@24.04"
}

variable "channel" {
  description = "Charm channel to use when deploying"
  type        = string
  default     = "16/stable"
}

variable "charm_name" {
  description = "Name of the charm on https://charmhub.io"
  type        = string
  default     = "postgresql"
  nullable    = false
}

variable "config" {
  description = "Application configuration. Details at https://charmhub.io/postgresql/configurations"
  type        = map(string)
  default     = {}
}

variable "constraints" {
  description = "Juju constraints to apply for this application."
  type        = string
  default     = "arch=amd64"
}

variable "enable_expose" {
  description = "Whether to expose the application"
  type        = bool
  default     = true
}

variable "juju_model" {
  description = "Deprecated: UUID of the Juju model. Use model_uuid instead"
  type        = string
  default     = null
}

variable "machine" {
  description = "Target Juju machine to deploy on"
  type        = string
  default     = null
}

variable "machines" {
  description = "Target Juju machines to deploy on"
  type        = set(string)
  default     = null

  validation {
    condition     = var.machines == null ? true : length(var.machines) > 0
    error_message = "machines must contain at least one machine id, or be left unset."
  }
}

variable "model_uuid" {
  description = "Juju model uuid"
  type        = string
  default     = null
}

variable "revision" {
  description = "Revision number to deploy charm"
  type        = number
  default     = null
}

variable "storage" {
  description = "Storage directive"
  type        = map(string)
  default     = {}
}

variable "units" {
  description = "Number of units to deploy"
  type        = number
  default     = 1
}
