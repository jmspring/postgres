"""
setup_postgis.py
A fabric file for setting up postgis 2.1 database on a freebsd server 
with postgres 9.3
- assumes a freebsd system with:
	- freebsd 9.2
	- python 2.7 installed
	- bash installed with symlink to /bin/bash pointing to
	  /usr/local/bin/bash
- configure the assorted DB_* variables as follows:
	DB_ADMIN - your db administrator
	DB_PASSWORD - the password for the db administrator
	DB_DATABASE - name of the database to create
"""

import sys
import random
import string
from fabric.api import env, sudo, cd, task, settings, run
from ilogue.fexpect import expect, expecting
from ilogue.fexpect import sudo as expectsudo

DB_ADMIN="pgdba"
DB_PASSWORD="apassword"
DB_DATABASE="postgisdb"

def _a_bit_of_random():
  charset = string.ascii_lowercase + string.digits
  r = ''.join(random.sample(charset * 6, 6))
  return r

# pkg is the path after /usr/ports/<X>
# - for instance postgis21 is databases/postgis21
def _install_package(pkg, args = None):
  with cd("/usr/ports/%s" % pkg):
    with settings(warn_only = True):
      result = run("PORT=`cat Makefile | grep -m 1 PORTNAME | awk '{print $2}'`; pkg_version | grep $PORT")
    if result.return_code != 0:
      sudo("make %s BATCH=yes" % ("" if args == None else args))
      sudo("make %s BATCH=yes install" % ("" if args == None else args))
    else:
      print >> sys.stderr, "Package already installed: %s" % pkg
      return False
  return True

# pkg is the path after /usr/ports/<X>
# - for instance postgis21 is databases/postgis21
def _clean_package(pkg):
  with cd("/usr/ports/%s" % pkg):
    sudo("make clean BATCH=yes")

def install_package(pkg, args = None):
  r = _install_package(pkg, args)
  if r:
    _clean_package(pkg)

def _install_postgres():
  install_package('databases/postgresql93-server')
  install_package('databases/postgresql93-client')
        
def _install_postgis():
  install_package('databases/postgis21', args='WITHOUT_X11=yes WITHOUT_TIFF=yes WITH_RASTER=on')

def _setup_postgres():
  # start at boot
  filename = "/tmp/foo_%s" % _a_bit_of_random()
  sudo("cat /etc/rc.conf | grep -v postgresql > %s" % filename)
  sudo("echo 'postgresql_enable=\"YES\"' >> %s" % filename)
  sudo("mv %s /etc/rc.conf" % filename)
  
  # init the db
  sudo("/usr/local/etc/rc.d/postgresql initdb")
  
  #start the db
  sudo("/usr/local/etc/rc.d/postgresql start")
  
  # create the user
  prompts = []
  prompts += expect("Enter password for new role:", DB_PASSWORD)
  prompts += expect("Enter it again:", DB_PASSWORD)
  with expecting(prompts):
    expectsudo("createuser -d -S -R -P %s" % DB_ADMIN, user="pgsql")

@task    
def create_database():
  # create the db
  with settings(warn_only = True):
    # create the table
    result = sudo("psql -l | grep %s" % DB_DATABASE, user="pgsql")
    if result.return_code != 0:
      sudo("createdb -O %s %s" % (DB_ADMIN, DB_DATABASE), user="pgsql")
    
    # add the language
    result = sudo("createlang -U %s -l %s | grep plpgsql" % (DB_ADMIN, DB_DATABASE), user="pgsql")
    if result.return_code != 0:     
      sudo("createlang -U %s plpgsql %s" % (DB_ADMIN, DB_DATABASE), user="pgsql")
  
  # enable postgis on db
  sudo('psql -U pgsql -d %s -c "CREATE EXTENSION IF NOT EXISTS postgis;"' % DB_DATABASE, user="pgsql")
  sudo('psql -U pgsql -d %s -c "CREATE EXTENSION IF NOT EXISTS postgis_topology;"' % DB_DATABASE, user="pgsql")

@task
def install_postgres():
  _install_postgres()
  _install_postgis()
  _setup_postgres()
