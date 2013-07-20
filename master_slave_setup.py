# Fabric script automating the deployment of a postgres master/slave setup
# using streaming replication.  The script assumes two systems installed with
# ubuntu 12.04 with current run of apt-get update.
#
# The config step sets up all the information necessary to configure and deploy
# the setup. 
#
# Because the setup needs to alternate between both the master and the slave,
# the script should be run as follows:
#
# fab -f ./master_slave_setup.py --set target=jims config install_postgres \
#       setup_postgres_user_connectivity configure_replication bringup_replication
#
#
# This script is based on the steps described in the 3DTin blog post located at:
#
# http://blog.3dtin.com/2012/07/26/postgresql-replication-and-hot-standby-in-practic/
#
# Thanks to them for doing the hard work.

import sys
import os

from fabric.api import env, sudo, env, put, run, settings, task, prompt
from fabric.contrib import files
from ilogue.fexpect import expect, expecting
from ilogue.fexpect import sudo as expectsudo

@task
def config():
  env.user = "gonzo"     # user w/ sudo access
  env.password = "fozzybear"     # user password
  env.db_master = "10.211.55.16"   # db master ip/hostname
  env.db_slave = "10.211.55.17"     # db slave ip/hostname
  env.db_userpwd = "fluffysock"     # postgres user unix pwd
  env.db_adminpwd = "ilikeshoes"    # postgres user db pwd
  env.db_replicatorpwd = "shovelingstuff"   # postgres replication user db pwd
  env.hosts = [ env.db_master, env.db_slave ]
  
def install_apt_packages(pkgs):
	if type(pkgs) is str:
		pkgs = [ pkgs ]
	for p in pkgs:
		gotit = 1
		with settings(warn_only = True):
			gotit = run('dpkg-query -l %s | grep "^ii" >& /dev/null' % p)
		if gotit.return_code != 0:
			sudo('apt-get install -y %s' % p)	    

def setup_postgres_server():
  packages = [
      "postgresql",
      "postgresql-contrib",
      "postgresql-client"
  ]
  
  install_apt_packages(packages)

def setup_postgres_user(userpwd, adminpwd):
  # set the postgres user password
  prompts = []
  prompts += expect('Enter new UNIX password:', userpwd)
  prompts += expect('Retype new UNIX password:', userpwd)
  with expecting(prompts):
    expectsudo('passwd postgres')
  # set the postgres db user password
  sudo("sudo -u postgres psql -d template1 -c \"ALTER USER postgres WITH PASSWORD '%s';\"" % adminpwd)
  # generate an ssh-key
  sudo("ssh-keygen -t rsa -b 4096 -N \"\" -f ~postgres/.ssh/id_rsa", user="postgres")
  
@task
def install_postgres():
  setup_postgres_server()
  setup_postgres_user(env.db_userpwd, env.db_adminpwd)
  
@task
def setup_postgres_user_connectivity():
  if env.host == env.db_master:
    srcIp = env.db_master
    destIp = env.db_slave
  else:
    srcIp = env.db_slave
    destIp = env.db_master

  prompts = []
  prompts += expect("Are you sure you want to continue connecting (yes/no)?", "yes")
  prompts += expect("postgres@%s's password:" % destIp, env.db_userpwd)
  with expecting(prompts):
    expectsudo("ssh-copy-id -i ~postgres/.ssh/id_rsa.pub postgres@%s" % destIp, user="postgres")  
    
@task
def configure_replication():
  # determine the PGDATA directory
  pgdata = sudo("pg_lsclusters | grep postgres | awk '{print $6}'")  
  pgversion = sudo("pg_lsclusters | grep postgres | awk '{print $1}'")
  pgconfDir = "/etc/postgresql/%s/main" % pgversion
  
  if env.host == env.db_master:
    srcIp = env.db_master
    destIp = env.db_slave
  else:
    srcIp = env.db_slave
    destIp = env.db_master
  
  if env.host == env.db_master:
    sudo("psql -c \"CREATE USER replicator SUPERUSER LOGIN CONNECTION LIMIT 1 ENCRYPTED PASSWORD '%s';\"" % env.db_replicatorpwd, user="postgres")
      
  # shut down the server and then edit configs
  sudo("service postgresql stop", pty=False)
  
  # update config
  # TODO -- make this replace inline...too lazy for now
  pgconfCfg = \
    "hot_standby = on\n" \
    "max_wal_senders = 1\n" \
    "wal_level = 'hot_standby'\n" \
    "archive_mode = on\n" \
    "archive_command = 'cd .'\n" \
    "listen_addresses = 'localhost,%s'" % env.host
  sudo("echo \"%s\" >> %s/postgresql.conf" % (pgconfCfg, pgconfDir))
  
  files.append("%s/pg_hba.conf" % pgconfDir, "host  replication replicator %s/32  md5" % destIp, use_sudo=True)

  
@task 
def bringup_replication():
  # first, bring up the master and sync data between master and slave
  pgdata = sudo("pg_lsclusters | grep postgres | awk '{print $6}'")
  pgport = 5432 #default postgres port
  pgversion = sudo("pg_lsclusters | grep postgres | awk '{print $1}'")
  pgconfDir = "/etc/postgresql/%s/main" % pgversion
  
  if env.host == env.db_master:
    sudo("service postgresql start")
    
  if env.host == env.db_master:
    srcIp = env.db_master
    destIp = env.db_slave
  else:
    srcIp = env.db_slave
    destIp = env.db_master  
  
  # write out backup script to both hosts, only run on the master
  # TODO - have script check for postgres user
  backupScript = [
    '#!/bin/bash',
    'BACKUP_LABEL="base_backup"',
    'psql -p %d -c "select pg_start_backup(\'$BACKUP_LABEL\');"' % pgport,
    'rsync -cva --inplace --exclude=*pg_xlog* %s/ -e ssh %s:%s/' % (pgdata, destIp, pgdata),
    'psql -p %d -c "select pg_stop_backup();"' % pgport
  ]
  i = 0;
  sudo("touch %s/base_backup.sh" % pgdata)
  files.append("%s/base_backup.sh" % pgdata, backupScript, use_sudo=True)
  
  sudo("chmod 744 %s/base_backup.sh" % pgdata)
  sudo("chown postgres:postgres %s/base_backup.sh" % pgdata)
  if env.host == env.db_master:
    sudo("%s/base_backup.sh" % pgdata, user="postgres", pty=False)
    
  # write out recovery file
  recoveryconf = "standby_mode = 'on'\n" \
    "primary_conninfo = 'host=%s port=%d user=replicator password=%s'\n" \
    "trigger_file = '/tmp/postgresql.trigger.%d'" % (destIp, pgport, env.db_replicatorpwd, pgport)
  sudo("echo \"%s\" >> %s/recovery.conf" % (recoveryconf, pgdata))
  
  # bring up slavered
  if env.host == env.db_slave:
    sudo("service postgresql start")