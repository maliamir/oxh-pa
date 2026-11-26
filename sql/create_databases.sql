-- Initialize databases for Oxbridge Health Prior Authorization API
-- Executed automatically on first PostgreSQL container startup via /docker-entrypoint-initdb.d
SELECT 'CREATE DATABASE oxh_prior_auth'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'oxh_prior_auth')\gexec

SELECT 'CREATE DATABASE oxh_prior_auth_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'oxh_prior_auth_test')\gexec
