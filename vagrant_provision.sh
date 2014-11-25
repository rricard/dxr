#!/bin/sh
# Shell script to provision the vagrant box
#
# This is idempotent, even though I'm not sure the shell provisioner requires
# it to be.

set -e
set -x

# Elasticsearch isn't in Debian proper yet, so we get it from
# elasticsearch.org's repo.
wget -qO - http://packages.elasticsearch.org/GPG-KEY-elasticsearch | apt-key add -
echo 'deb http://packages.elasticsearch.org/elasticsearch/1.1/debian stable main' > /etc/apt/sources.list.d/elasticsearch.list

apt-get update
# clean out redundant packages from vagrant base image
apt-get autoremove -y

#configure locales:
apt-get install -y language-pack-en

# node and npm:
apt-get install -y npm
# Homogenize binary name with production RHEL:
ln -sf /usr/bin/nodejs /usr/local/bin/node

# Docs build system:
apt-get install -y sphinx-common

# Python:
apt-get install -y libapache2-mod-wsgi python-pip
pip install virtualenv virtualenvwrapper nose
cd ~vagrant/dxr
./peep.py install -r requirements.txt
python setup.py develop

# A few debugging tools:
pip install pdbpp nose-progressive
if [ ! -e ~vagrant/.pdbrc.py ]; then
    cat >~vagrant/.pdbrc.py <<THEEND
from pdb import DefaultConfig


class Config(DefaultConfig):
   #highlight = False
   current_line_color = 43
   sticky_by_default = True
   bg = 'light'
THEEND
fi

# DXR itself:
# pkg-config is so (trilite's?) make clean works.
apt-get install -y git llvm-3.5 libclang-3.5-dev clang-3.5 pkg-config
# --force overrides any older-version LLVM alternative lying around, letting
# us upgrade by provisioning rather than destroying the whole box:
update-alternatives --force --install /usr/local/bin/llvm-config llvm-config /usr/bin/llvm-config-3.5 0

# Elasticsearch:
apt-get install -y openjdk-7-jdk elasticsearch
# Make it keep to itself, rather than forming a cluster with everything on the
# subnet:
sed -i 's/# \(discovery\.zen\.ping\.multicast\.enabled: false\)/\1/' /etc/elasticsearch/elasticsearch.yml
sed -i 's/# network\.bind_host: 192\.168\.0\.1/network.bind_host: 127.0.0.1/' /etc/elasticsearch/elasticsearch.yml
# Cut RAM so it doesn't take up the whole VM. This should be MUCH bigger for
# production.
sed -i 's/#ES_HEAP_SIZE=2g/ES_HEAP_SIZE=128m/' /etc/init.d/elasticsearch
# And don't swap:
sed -i 's/# \(bootstrap\.mlockall: true\)/\1/' /etc/elasticsearch/elasticsearch.yml
# Come up on startup:
update-rc.d elasticsearch defaults 95 10
[ ! -d /usr/share/elasticsearch/plugins/lang-javascript ] && /usr/share/elasticsearch/bin/plugin -install elasticsearch/elasticsearch-lang-javascript/2.1.0
/etc/init.d/elasticsearch start
