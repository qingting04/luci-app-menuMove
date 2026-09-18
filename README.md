# luci-app-menuMove（菜单标签移动）

ImmortalWrt / OpenWrt 的 LuCI 插件：**把网页界面里的菜单条目（标签页）移动到另一个分区**。

典型用法就是把「NAS」分区下面的标签（NFS、Aria2、Samba …）挪到「Services（服务）」下面，
也可以整段移动一个分区、只改排序、或者给标签换个名字。

```
移动前                                    移动后
Nas                                      Services
├─ Network Shares   admin/nas/samba       ├─ Terminal (custom)  admin/services/ttyd
├─ NFS              admin/nas/nfs         ├─ Samba              admin/services/samba4
├─ Aria2            admin/nas/aria2       ├─ NFS                admin/services/nfs   ← 移过来了
│  └─ Log           admin/nas/aria2/log   ├─ Aria2              admin/services/aria2
└─ Terminal         admin/nas/ttyd        │  └─ Log             admin/services/aria2/log
                                          └─ OpenClash          admin/services/openclash
Status
└─ Network Shares   admin/status/samba    （原来的 Nas 分区因为空了，自动不再显示）
```

## 1. 原理（为什么需要这么绕）

LuCI 的菜单结构由每个插件自己的 `/usr/share/luci/menu.d/*.json` 定义，文件名即菜单路径，例如：

```json
{
  "admin/nas/nfs": {
    "title": "NFS",
    "order": 30,
    "action": { "type": "view", "path": "nas/nfs" },
    "depends": { "acl": [ "luci-app-nfs" ] }
  }
}
```

LuCI 在每次请求时按**文件名顺序**读取并合并这些 JSON（`modules/luci-base/ucode/dispatcher.uc`
的 `build_pagetree()`），同名路径的后一个文件只能覆盖 `title` / `order` / `action` / `depends`
等属性，**无法改变路径本身**（= 无法改位置）。而且 LuCI 没有“隐藏节点”的属性开关，只有
`depends` 检查不通过时该条目才会被隐藏（前端 `ui.js` 的 `getChildren()` 会跳过
`satisfied === false` 的节点）。

所以本插件的做法是：

1. **克隆**：把原条目（以及它的整棵子树）复制一份，注册到目标分区下
   （`action`、`depends`、标题等原样保留，排序/名称可选覆盖）；
2. **隐藏**：给原条目加一个永远不会满足的 `depends` 守卫
   （`{"fs": [ { "/usr/lib/luci-menu-move/.disabled": "file" } ]}`），使它不再显示。

两条合成一个覆盖文件 `/usr/share/luci/menu.d/zz-luci-app-menuMove.json`（`zz-` 保证它排在最后、
优先级最高）。LuCI 的菜单缓存以文件列表的 inode/mtime 做 key，所以文件一写就自动失效，**刷新浏览器即可生效**。

> 小彩蛋：想临时把所有被隐藏的条目放出来，`touch /usr/lib/luci-menu-move/.disabled` 即可（删掉又隐藏）。

## 2. 文件结构

```
luci-app-menuMove/
├── Makefile                                   # luci.mk 包定义（opkg / apk 都能编）
├── ucode/menu-move.uc                         # 核心逻辑（路径扫描 / 合并 / 生成 JSON）
├── htdocs/luci-static/resources/view/menuMove/overview.js   # LuCI 网页界面
├── po/{templates,zh_Hans}/                    # 中英文翻译
├── root/
│   ├── etc/config/menu-move                   # UCI 配置（规则）
│   ├── etc/init.d/menu-move                   # procd 触发器：配置一改就重新生成
│   ├── etc/uci-defaults/90-luci-app-menuMove  # 安装后立即生成一次
│   ├── usr/bin/menu-move                      # 命令行工具（SSH 用）
│   ├── usr/share/rpcd/ucode/menu-move         # ubus 对象 menu_move（Web 界面调用）
│   ├── usr/share/luci/menu.d/luci-app-menuMove.json  # 本插件自己的菜单入口
│   └── usr/share/rpcd/acl.d/luci-app-menuMove.json   # 权限声明
└── test/                                      # 本地测试（不需要路由器）
    ├── run.sh  test.uc                        # 单元测试 + CLI 冒烟测试
    ├── simulate_menu.py                       # 复刻 LuCI 服务端+前端菜单算法做仿真验证
    └── fixtures/                              # 假的 menu.d / UCI / lua 控制器
```

## 3. 安装

### 3.1 用 SDK / 源码编译（推荐）

把整个 `luci-app-menuMove/` 目录放进：

- 源码树：`<openwrt>/package/luci-app-menuMove/`（或自定义 feed 的 `applications/` 下）
- SDK：`<sdk>/package/luci-app-menuMove/`

```sh
make package/luci-app-menuMove/compile V=s
```

生成的 ipk/apk 在 `bin/packages/*/luci/` 下，拷到路由器后：

```sh
opkg install luci-app-menuMove_*.ipk     # 24.10 及更早
apk add --allow-untrusted luci-app-menuMove*.apk   # 25.xx（apk）上
```

### 3.2 不编译，直接手动装（临时试验）

```sh
# 在路由器上执行（把目录按 root/ 的结构铺开）
scp -r root/* root@192.168.1.1:/
scp ucode/menu-move.uc root@192.168.1.1:/usr/share/ucode/
scp -r htdocs/* root@192.168.1.1:/www/          # htdocs/luci-static/... → /www/luci-static/...
ssh root@192.168.1.1 'chmod +x /usr/bin/menu-move /etc/init.d/menu-move; \
  /etc/init.d/menu-move enable; /etc/init.d/rpcd reload; /usr/bin/menu-move apply'
```

依赖（一般已随 LuCI 安装）：`luci-base`、`rpcd-mod-ucode`、`ucode-mod-fs`、`ucode-mod-uci`。

## 4. 使用

### 4.1 网页界面

`系统 (System) → 菜单标签 (Menu Tabs)`：

- **常规**：总开关。
- **移动规则**：每条规则 = 要移动的标签（原路径）+ 目标分区 + 排序 + 新名称（可选）+ 隐藏原位置。
  下拉框列出的是**当前实际存在的**菜单条目，不用记路径。
- **状态**：显示覆盖文件是否存在、是否已过期，以及两个按钮：
  「立即重新生成」和「刷新界面」。
- 底部 `保存` / `保存并应用`：保存后会自动提交 UCI 并重新生成菜单，然后刷新页面。

### 4.2 命令行

```sh
menu-move status     # 显示配置状态、文件是否存在、是否过期
menu-move apply      # 重新生成覆盖文件
menu-move check      # 试运行，只报告会做什么/有哪些错误
menu-move paths      # 列出当前所有菜单路径（用来写规则）
menu-move json       # 输出完整状态 + 计划（JSON，便于脚本处理）
```

### 4.3 UCI（脚本化配置）

```sh
uci add menu-move move
uci set menu-move.@move[-1].from='admin/nas/nfs'
uci set menu-move.@move[-1].to='admin/services'
uci set menu-move.@move[-1].order='45'
uci set menu-move.@move[-1].hide_original='1'
uci commit menu-move
/usr/bin/menu-move apply        # commit 之后 procd 触发器通常已经自动跑过一次
```

规则字段：

| 字段 | 说明 |
| --- | --- |
| `from` | 原菜单路径，如 `admin/nas/nfs` |
| `to` | 目标分区路径，如 `admin/services`；填原路径的父级 = 原地调整排序 |
| `order` | 在目标分区内的排序权重，越小越靠前；留空 = 保持原值 |
| `title` | 可选，覆盖显示名称（留空 = 原名） |
| `hide_original` | 是否隐藏原位置（默认 `1`） |
| `enabled` | 单条规则开关 |

## 5. 注意事项与已知限制

1. **原地址会失效**：被隐藏的条目不再是合法路由，直接访问旧 URL
   （如 `/cgi-bin/luci/admin/nas/nfs`）会落到同分区的其它页面。书签请用新地址。
2. **Lua controller 定义的应用**（老式 `luci.controller.*`，如某些 OpenClash 版本）：
   Lua controller 在 JSON 之后加载，会覆盖本插件的 JSON，隐藏可能无效。
   插件会检测并在界面/命令行给出警告。
3. **权限沿用原条目**：克隆出来的条目的 `depends.acl` 与原条目一致，所以只有原本能看到该页面的用户
   才能看到移动后的标签。
4. **不会自动跟随插件升级**：新装插件后菜单变化了，点一下「立即重新生成」，
   或看「状态」里的过期提示（`menu-move status` 也会显示）。
5. **sysupgrade**：`/usr/share` 不在备份里，覆盖文件重启后由 init 脚本自动重建（规则在 `/etc/config`，会保留）。
6. 只能把标签加到**已存在的菜单路径**下面；不存在的目标会在界面上被拒绝（因为凭空造出的分区没有标题，
   前端根本不会显示）。

## 6. 测试

不需要路由器，装一个 `ucode` 即可（本地或 host 编译产物都行）：

```sh
UCODE=/path/to/ucode ./test/run.sh
```

会做三件事：

1. `test/test.uc`：60 项单元检查（合并语义、克隆、子树、原地排序、错误路径、幂等、UCI 开关、Lua 检测）；
2. CLI 冒烟测试（`status` / `check` / `paths` / `apply`）；
3. `test/simulate_menu.py`：按 LuCI `dispatcher.uc` 的 `build_pagetree()` 与前端 `ui.js` 的
   `scrubMenu()`/`getChildren()` 复刻一套仿真，验证**移动后的菜单真的长成期望的样子**
   （移动的文件、被隐藏的原条目、整棵子树、旧 URL 消失、分区清空后自动隐藏）。

## 7. 命名约定（和 jluDrcom 一致）

- **包名 / LuCI 侧标识：小驼峰** —— `luci-app-menuMove`、菜单路径 `admin/system/menuMove`、
  视图 `menuMove/overview`、权限组 `luci-app-menuMove`；
- **OS 机制名：kebab-case** —— UCI 配置 `/etc/config/menu-move`、命令行 `/usr/bin/menu-move`、
  ubus 对象 `menu_move`、init 脚本 `/etc/init.d/menu-move`、生成的文件
  `zz-luci-app-menuMove.json`、隐藏标记 `/usr/lib/luci-menu-move/.disabled`。

改包名只需改目录名 + `PKG_NAME`；`luci.mk` 会用目录名推导 `LUCI_BASENAME`（这里是 `menuMove`），
所以翻译包叫 `luci-i18n-menuMove-zh-cn`。

## 8. 仓库与 CI

远程仓库：<git@github.com:qingting04/luci-app-menuMove.git>

`.github/workflows/build.yml` 沿用 jluDrcom 的流水线：下载 ImmortalWrt SDK
（25.12.2 / ramips-mt7621 / gcc 14.3.0）→ 把本目录拷进 SDK 的 `package/` → `feeds update/install`
→ `make package/luci-app-menuMove/compile`，产物上传为 Actions artifact：

```
luci-app-menuMove*.apk                    （主包）
luci-i18n-menuMove-zh-cn*.apk             （中文翻译包，工作流里已打开 LUCI_LANG_zh_Hans）
```

手动触发：仓库 → Actions → build → Run workflow。

## 9. 卸载 / 恢复

```sh
menu-move status            # 或者直接把总开关关掉，然后保存应用
uci set menu-move.settings.enabled='0'; uci commit menu-move; /usr/bin/menu-move apply
rm -f /usr/share/luci/menu.d/zz-luci-app-menuMove.json     # 彻底恢复
```

## 10. 许可

Apache-2.0（与 LuCI 一致）。
