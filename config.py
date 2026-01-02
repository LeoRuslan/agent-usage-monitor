"""Configuration constants for usage monitor."""

import os

# Antigravity endpoints
ANTIGRAVITY_GETUNLEASH_PATH = "/exa.language_server_pb.LanguageServerService/GetUnleashData"
ANTIGRAVITY_GETUSERSTATUS_PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
ANTIGRAVITY_GETCOMMANDMODELCONFIGS_PATH = "/exa.language_server_pb.LanguageServerService/GetCommandModelConfigs"

# Gemini settings
GEMINI_QUOTA_ENDPOINT = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
DEFAULT_GEMINI_CREDS = os.path.expanduser("~/.gemini/oauth_creds.json")
DEFAULT_GEMINI_SETTINGS = os.path.expanduser("~/.gemini/settings.json")

# Common settings
DEFAULT_TIMEOUT = 8.0
VERIFY_SSL = False
