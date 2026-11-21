-- Runs once on first start of mysql57 and mysql80. Local credentials only.
CREATE DATABASE IF NOT EXISTS t2s_business
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS t2s_business._t2s_probe (
  id INT NOT NULL PRIMARY KEY,
  note VARCHAR(32) NOT NULL
) ENGINE=InnoDB;

INSERT INTO t2s_business._t2s_probe (id, note) VALUES (1, 'probe');

-- mysql_native_password lets PyMySQL authenticate on 8.0 without the
-- `cryptography` package (caching_sha2_password over plain TCP needs RSA).
CREATE USER IF NOT EXISTS 't2s_ro'@'%'
  IDENTIFIED WITH mysql_native_password BY 't2s_ro_local_only';
GRANT SELECT ON t2s_business.* TO 't2s_ro'@'%';
FLUSH PRIVILEGES;
