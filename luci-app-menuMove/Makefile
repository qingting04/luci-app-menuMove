#
# This is free software, licensed under the Apache License, Version 2.0 .
#

include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-menuMove
PKG_VERSION:=1.0.0
PKG_RELEASE:=13
PKG_LICENSE:=Apache-2.0
PKG_MAINTAINER:=qingting04

LUCI_TITLE:=LuCI menu tab organizer
LUCI_DESCRIPTION:=Move LuCI menu tabs (entries) of the web interface to another section, \
for example move everything below "NAS" into "Services".
LUCI_DEPENDS:=+luci-base +rpcd-mod-file +rpcd-mod-ucode +ucode-mod-fs +ucode-mod-uci +procd
LUCI_PKGARCH:=all

define Package/$(PKG_NAME)/conffiles
/etc/config/menu-move
endef

define Package/$(PKG_NAME)/postinst
#!/bin/sh

[ -n "$${IPKG_INSTROOT}" ] || {
	/etc/init.d/menu-move enable 2>/dev/null
	/usr/bin/menu-move apply 2>/dev/null
	rm -f /tmp/luci-indexcache.*
	rm -rf /tmp/luci-modulecache/
	/etc/init.d/rpcd reload 2>/dev/null
	exit 0
}
endef

define Package/$(PKG_NAME)/prerm
#!/bin/sh

[ -n "$${IPKG_INSTROOT}" ] || {
	/etc/init.d/menu-move stop 2>/dev/null
	/etc/init.d/menu-move disable 2>/dev/null
	exit 0
}
endef

define Package/$(PKG_NAME)/postrm
#!/bin/sh

[ -n "$${IPKG_INSTROOT}" ] || {
	rm -f /usr/share/luci/menu.d/zz-luci-app-menuMove.json
	rm -f /tmp/luci-indexcache.*
	exit 0
}
endef

# luci.mk lives either in the root of the luci feed (package placed in-tree,
# i.e. <luci-feed>/applications/luci-app-menuMove) or in the cloned luci feed
# (feeds/luci/luci.mk) - the usual setup when the package sits in package/.
LUCI_MK:=$(firstword $(wildcard $(CURDIR)/../../luci.mk) $(wildcard $(TOPDIR)/feeds/luci/luci.mk))
ifeq ($(LUCI_MK),)
  $(error luci.mk not found - run "scripts/feeds update -a && scripts/feeds install -a" first)
endif
include $(LUCI_MK)

# call BuildPackage - OpenWrt buildroot signature
