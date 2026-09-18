module("luci.controller.openclash", package.seeall)

function index()
	if not nixio.fs.access("/etc/config/openclash") then
		return
	end

	entry({"admin", "services", "openclash"}, cbi("openclash"), _("OpenClash"), 90).dependent = true
end
