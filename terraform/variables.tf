variable "app_name" {
  description = "Name of the application in the Juju model."
  type        = string
  default     = "postgresql"
}

variable "base" {
  description = "Application base"
  type        = string
  default     = "ubuntu@22.04"
}

variable "channel" {
  description = "Charm channel to use when deploying"
  type        = string
  default     = "14/stable"
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
  type        = bool
  default     = true
  description = "Whether to expose the application"
}

variable "juju_model" {
  description = "Deprecated: UUID of the Juju model. Use model_uuid instead"
  type        = string
  default     = null
}

variable "model_uuid" {
  description = "UUID of the Juju model to deploy to"
  type        = string
  default     = null
}

variable "revision" {
  description = "Revision number to deploy charm"
  type        = number
  default     = null
}

variable "storage_size" {
  description = "Storage size"
  type        = string
  default     = "10G"
}

variable "units" {
  description = "Number of units to deploy"
  type        = number
  default     = 1
}
