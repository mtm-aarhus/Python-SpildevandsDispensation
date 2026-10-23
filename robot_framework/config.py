"""This module contains configuration constants used across the framework"""

# The number of times the robot retries on an error before terminating.
MAX_RETRY_COUNT = 3

QUEUE_ATTEMPTS = 2

# Whether the robot should be marked as failed if MAX_RETRY_COUNT is reached.
FAIL_ROBOT_ON_TOO_MANY_ERRORS = True

# Error screenshot config
SMTP_SERVER = "smtp.adm.aarhuskommune.dk"
SMTP_PORT = 25
SCREENSHOT_SENDER = "spildevandsdispensation@aarhus.dk"

# Constant/Credential names
ERROR_EMAIL = "Error Email"
# Username = https://adressevaelger.dk, password = token
ADRESSEVAELGER_CREDENTIAL = "Adressevælger"
# Username = https://graphql.datafordeler.dk/flexible/v3, password = API key
DATAFORDELER_CREDENTIAL = "DatafordelerAPI"
# Username/password of the SQL user in ApiDataStorage (SELECT + INSERT on the one table)
SQL_CREDENTIAL = "SpildevandsSQLUser"
# The SQL server SpatialMap reads from
SQL_SERVER_CONSTANT = "SpildevandsSQLURL"

# The database on that server
SQL_DATABASE = "ApiDataStorage"

# Written to AddedBy, so rows from this robot can be told from the old API's ("ditmerflex")
ADDED_BY = "xflow-oo"


# Queue specific configs
# ----------------------

# The name of the job queue (if any)
QUEUE_NAME = "SpildevandsDispensation"

# The limit on how many queue elements to process
MAX_TASK_COUNT = 100

# ----------------------
