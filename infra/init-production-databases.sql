\set ON_ERROR_STOP on
\getenv auth_password AUTH_DB_PASSWORD
\getenv schedule_password SCHEDULE_DB_PASSWORD
\getenv queue_password QUEUE_DB_PASSWORD
\getenv notifications_password NOTIFICATIONS_DB_PASSWORD

CREATE ROLE auth LOGIN PASSWORD :'auth_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 6;
CREATE ROLE schedule LOGIN PASSWORD :'schedule_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 6;
CREATE ROLE queue LOGIN PASSWORD :'queue_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 6;
CREATE ROLE notifications LOGIN PASSWORD :'notifications_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 6;

CREATE DATABASE auth OWNER auth;
CREATE DATABASE schedule OWNER schedule;
CREATE DATABASE queue OWNER queue;
CREATE DATABASE notifications OWNER notifications;
REVOKE ALL ON DATABASE auth, schedule, queue, notifications, postgres, template1 FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE auth TO auth;
GRANT CONNECT, TEMPORARY ON DATABASE schedule TO schedule;
GRANT CONNECT, TEMPORARY ON DATABASE queue TO queue;
GRANT CONNECT, TEMPORARY ON DATABASE notifications TO notifications;

ALTER ROLE auth SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE schedule SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE queue SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE notifications SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE auth SET statement_timeout = '30s';
ALTER ROLE schedule SET statement_timeout = '30s';
ALTER ROLE queue SET statement_timeout = '30s';
ALTER ROLE notifications SET statement_timeout = '30s';
ALTER ROLE auth SET lock_timeout = '10s';
ALTER ROLE schedule SET lock_timeout = '10s';
ALTER ROLE queue SET lock_timeout = '10s';
ALTER ROLE notifications SET lock_timeout = '10s';
