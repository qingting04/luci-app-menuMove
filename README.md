# luci-app-menuMove

ImmortalWrt / OpenWrt 的 LuCI 插件：**把网页界面的菜单条目（标签页）移动到另一个分区** —— 例如把「NAS」下面的标签挪到「Services」下面。

可视化配置、带过期检测和命令行工具，支持 GitHub Actions 云端编译（无需本地搭建 OpenWrt 环境）。

## 原理

LuCI 的菜单由每个插件自己的 `/usr/share/luci/menu.d/*.json` 定义，**文件名就是菜单路径**，例如 `admin/nas/nfs`：

```json
{
  "admin/nas/nfs": {
    "title": "NFS",
    "order": 30,
    "action": { "type": "view", "path": "nas/nfs" }
  }
}
```

LuCI 每次请求时按文件名顺序合并这些 JSON（`luci-base` 的 `dispatcher.uc` → `build_pagetree()`），同名路径的后一个文件只能覆盖 `title` / `order` / `action` / `depends`，**改不了路径本身**（＝改不了位置）；而且 LuCI 没有「隐藏节点」的开关，只有 `depends` 检查不通过时条目才会被隐藏。

所以本插件的做法是：

1. **克隆**：把原条目（连同整棵子树）复制一份，注册到目标分区下，`action` / `depends` / 标题原样保留，排序和名称可以覆盖；
2. **隐藏**：给原条目加一个永远不满足的 `depends` 守卫，让原位置不再显示。

两条合成一个覆盖文件 `/usr/share/luci/menu.d/zz-luci-app-menuMove.json`（`zz-` 保证它排在最后、优先级最高）。LuCI 的菜单缓存以文件列表的 inode/mtime 为 key，文件一写就自动失效，**刷新浏览器即生效**。

写入是**原子**的（先写同目录的 `.tmp` 再 `rename`，不会出现「读到半个 JSON」的窗口），而且**内容没变就不重写** —— 否则每次开机都会白白刷新 mtime、把 LuCI 的菜单缓存清掉一次。「是否过期」也是按**内容**判断：直接把「现在应该生成什么」算出来跟磁盘上已有的比，所以哪怕新装的插件带的时间戳比覆盖文件还旧（apk 里通常是构建时间），一样能查出来。

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
└─ Network Shares   admin/status/samba    （原 Nas 分区空了，自动不再显示）
```

> 彩蛋：`touch /usr/lib/luci-menu-move/.disabled` 可以临时把所有被隐藏的条目放出来（删掉又隐藏）。

## 特性

- 可视化配置：下拉框直接列出**当前真实存在的菜单路径**，不用背路径、不会写错
- 支持整段移动（连同子树）、原地调整排序、改显示名、隐藏原位置
- 状态面板：覆盖文件是否存在 / 是否已过期（**按内容比对，不靠 mtime**），一键「立即重新生成」
- 覆盖文件原子写入 + 内容不变不重写：不会误伤 LuCI 的菜单缓存
- 网页「保存并应用」和「立即重新生成」都直接调用 `/usr/bin/menu-move`（走系统自带的 `file` ubus 对象），**不依赖本插件自己的 ubus 插件**；命令的报错原文会直接显示在页面上，方便定位
- SSH 里 `uci commit menu-move` 会自动触发重新生成（procd `config.change` 触发器），也可以手动 `/etc/init.d/menu-move reload`
- 权限沿用原条目（克隆时保留 `depends.acl`），移动后的标签谁能看见跟原来一致
- 命令行：`menu-move status | apply | check | paths | json`
- 中文界面（`zh_Hans` 翻译包）
- GitHub Actions 云端编译，本地零环境；本地 60+12 项测试 + `test/check.py` 一致性检查，不需要路由器

## 快速开始：fork 编译（推荐，无需本地环境）

### 1. Fork 本仓库

点右上角 **Fork**。

### 2. 改成你自己的路由器架构

插件是 `PKGARCH:=all`（纯脚本包），**任意 target 的 SDK 都能编出通用包**，所以这四行一般不用动；默认值是**小米路由器 3G（MT7621）**：

```yaml
env:
  SDK_VERSION: "25.12.2"   # ImmortalWrt 版本
  TARGET: "ramips"         # 目标平台
  SUBTARGET: "mt7621"      # 子目标
  GCC: "14.3.0"            # gcc 版本
```

### 3. 触发编译

- 直接 push 到 `main` 分支，自动触发；或
- 仓库 **Actions** 标签页 → 左侧 `build` → **Run workflow** 手动触发。

约 5~10 分钟完成。

### 4. 下载产物

Actions → 最近一次运行 → **Summary** → 下载 `luci-app-menuMove` artifact，解压得到：

```
luci-app-menuMove-1.0.0-r5.apk           # 主包
luci-i18n-menuMove-zh-cn-1.0.0-r5.apk              # 中文翻译包（workflow 里已打开 LUCI_LANG_zh_Hans）
```

### 5. 装到路由器

```sh
scp luci-app-menuMove_*.apk luci-i18n-menuMove-zh-cn-1.0.0-r5.apk root@192.168.1.1:/tmp/
ssh root@192.168.1.1
apk add --allow-untrusted /tmp/luci-app-menuMove_*.apk
apk add --allow-untrusted /tmp/luci-i18n-menuMove-zh-cn-1.0.0-r5.apk
```

> `--allow-untrusted` 是因为本地/CI 编译的包没有官方签名。
> ImmortalWrt 24.10 及更早（opkg）用 `opkg install /tmp/luci-app-menuMove_*.ipk`。

装完不需要重启，直接刷新 LuCI，在 **服务 → 菜单标签** 里配置。

## 本地编译（有 OpenWrt / ImmortalWrt 源码树时）

```sh
cp -r luci-app-menuMove immortalwrt/package/
cd immortalwrt

./scripts/feeds update -a && ./scripts/feeds install -a   # 首次，拉 feeds 源码

make menuconfig   # 选架构 + 勾选 LuCI → Applications → luci-app-menuMove
make package/luci-app-menuMove/compile V=s
```

产物在 `bin/packages/<架构>/luci/` 下。

## 使用

打开 LuCI（`http://192.168.1.1`）→ **服务 → 菜单标签**：

- **常规**：总开关（关掉就恢复原始菜单）
- **移动规则**：每条规则 = 要移动的标签（原路径）+ 目标分区 + 排序 + 新名称（可选）+ 隐藏原位置
- **状态**：「立即重新生成」/「刷新界面」按钮，以及覆盖文件是否存在、是否过期；命令报错会原样显示在这里
- 展开可以看**当前所有菜单条目**（路径 / 标题 / 类型），写规则时对照着看

保存 / 保存并应用后会自动重新生成菜单并刷新页面。

### 可配置项

网页界面的每行规则对应 `/etc/config/menu-move` 里的一个 `config move` 段：

| 字段 | 默认值 | 说明 |
|---|---|---|
| `from` | —— | 要移动的菜单路径，如 `admin/nas/nfs` |
| `to` | —— | 目标分区路径，如 `admin/services`；填原路径的父级 = 原地调整排序 |
| `order` | 保持原值 | 在目标分区内的排序权重，越小越靠前 |
| `title` | 空（保持原名） | 用另一个名字显示该标签 |
| `hide_original` | `1` | 是否隐藏原位置 |
| `enabled` | `1` | 单条规则开关 |
| `settings.enabled` | `1` | 总开关，关闭时删除覆盖文件、恢复原菜单 |

把「Nas 下的 NFS」移到「Services」，命令行等价：

```sh
uci add menu-move move
uci set menu-move.@move[-1].from='admin/nas/nfs'
uci set menu-move.@move[-1].to='admin/services'
uci set menu-move.@move[-1].order='45'
uci commit menu-move
/etc/init.d/menu-move reload        # commit 时 procd 触发器通常已经跑过，这条是保险
```

### 命令行

| 命令 | 说明 |
|---|---|
| `menu-move status` | 配置状态、覆盖文件是否存在、是否过期 |
| `menu-move apply` | 立即重新生成覆盖文件 |
| `menu-move check` | 试运行：只报告会做什么、有哪些错误，不写文件 |
| `menu-move paths` | 列出当前所有菜单路径（写规则时用） |
| `menu-move json` | 输出完整状态 + 计划（JSON，便于脚本处理） |

## 目录结构

```
.
├── luci-app-menuMove/                       # LuCI 插件包
│   ├── Makefile                             #   luci.mk 包定义（opkg / apk 都能编）
│   ├── ucode/menuMove.uc                   #   核心逻辑（luci.mk 安装为 /usr/share/ucode/luci/menuMove.uc，导入名 luci.menuMove）
│   ├── htdocs/luci-static/resources/view/menuMove/overview.js   # 网页界面
│   ├── po/{templates,zh_Hans}/              #   中英文翻译
│   ├── root/
│   │   ├── etc/config/menu-move             #   UCI 配置（规则）
│   │   ├── etc/init.d/menu-move             #   procd 触发器：配置一改就重新生成
│   │   ├── etc/uci-defaults/90-luci-app-menuMove   # 安装后立即生成一次
│   │   ├── usr/bin/menu-move                #   命令行工具（网页界面也调它）
│   │   ├── usr/share/rpcd/ucode/luci.menuMove   # ubus 对象 menu_move（可选 API，界面不依赖它）
│   │   ├── usr/share/luci/menu.d/luci-app-menuMove.json   # 本插件自己的菜单入口
│   │   └── usr/share/rpcd/acl.d/luci-app-menuMove.json    # 权限声明（uci + file exec + ubus）
│   └── test/                                # 本地测试（不需要路由器）
│       ├── run.sh  test.uc                  #   60 项单元检查 + CLI/插件冒烟
│       ├── simulate_menu.py                 #   复刻 LuCI 服务端+前端菜单算法做仿真验证（12 项）
│       ├── check.py                         #   翻译覆盖 / ACL 与视图调用一致性 / 模块名 / JSON / JS 语法
│       ├── apk_info.py                      #   列出 apk 内文件（CI 用它打印装机路径）
│       └── fixtures/                        #   假的 menu.d / UCI / Lua controller
└── .github/workflows/build.yml              # GitHub Actions 云端编译
```

## 本地测试（可选，不需要路由器）

装一个 `ucode` 就能跑（宿主机或 host 编译产物都行）：

```sh
cd luci-app-menuMove
UCODE=/path/to/ucode ./test/run.sh
```

三部分：`test.uc` 的 60 项单元检查（合并语义、克隆、子树、原地排序、非法规则、幂等、开关、Lua 检测）→ CLI/插件冒烟测试 → `simulate_menu.py` 按 `dispatcher.uc` 与前端 `ui.js` 的算法复刻仿真，验证移动后的菜单真的长成期望的样子。

## 架构映射

fork 后如需修改 workflow 顶部的 4 个变量（纯 LuCI 插件其实不挑 target，默认值可直接用）：

| 路由器 | TARGET / SUBTARGET |
|---|---|
| 小米 3G / 4A 千兆 / AC2100 等（MT7621） | `ramips` / `mt7621` |
| 小米 4A 百兆 / 4C 等（MT7628） | `ramips` / `mt76x8` |
| x86_64 软路由 | `x86` / `64` |
| 树莓派 4 | `bcm27xx` / `bcm2711` |

> 不确定时：在 [ImmortalWrt 固件下载站](https://downloads.immortalwrt.org) 找到你的机型，看它在 `releases/<版本>/targets/<TARGET>/<SUBTARGET>/` 的哪一层。`GCC` 版本看该目录下 `immortalwrt-sdk-*.tar.zst` 文件名里的 `gcc-xx.x.x`。

## 注意事项

1. **原地址会失效**：被隐藏的条目不再是合法路由，直接访问旧 URL（如 `/cgi-bin/luci/admin/nas/nfs`）会返回 404，书签请用新地址。
2. **Lua controller 定义的应用**（老式 `luci.controller.*`，如某些 OpenClash 版本）在 JSON 之后加载，会覆盖本插件，隐藏可能无效 —— 插件会检测并在界面/命令行给出警告。
3. **新装/卸载插件后菜单变了**：界面「状态」区会立刻提示「生成的文件已过期」（按内容比对，与文件时间戳无关），点一下「立即重新生成」即可；`menu-move status` 的 `stale` 同理。
4. **`/usr/share` 不在 sysupgrade 备份里**，重启/升级后覆盖文件由 init 脚本自动重建（规则在 `/etc/config/menu-move`，会保留）。
5. 只能把标签挂到**已存在的菜单路径**下，目标不存在会被拒绝（凭空造出的分区没有标题，前端根本不显示）。
6. 需要 `luci-base`、`rpcd-mod-file`、`rpcd-mod-ucode`、`ucode-mod-fs`、`ucode-mod-uci`（装 LuCI 时通常已有，包依赖里已声明）。`rpcd-mod-file` 是网页界面调用命令行的通道，`rpcd-mod-ucode` 只有可选的 ubus 对象用得到。

## 说明

- **包名 / LuCI 侧标识是小驼峰**：`luci-app-menuMove`、菜单路径 `admin/services/menuMove`、视图 `menuMove/overview`、权限组 `luci-app-menuMove`；
  但 **UCI config 名、ubus 对象名、init.d 脚本名、命令行名保持 kebab-case**：`menu-move` / `menu_move`（OpenWrt 系统机制约定）。
- **LuCI 版本**：JS 框架需 LuCI 23.05+，ImmortalWrt 23.05 / 24.10 / 25.x 均支持。
- **ucode 语法保持保守**：模块与 CLI 只用老固件都支持的写法 —— 不用空值合并（两个问号）、可选链、模板字符串，也不用 ucode 特有的多变量 for-in。路由器上曾因为用了这些写法而编译不过，报的是一串 `Expecting ';'` 语法错（当时界面上只能看到「无法访问插件」，非常难查）。`test/check.py` 第 7 节会静态检查这一条，CI 里会跑。
- **为什么网页界面不直接用 ubus 插件**：`fs.exec('/usr/bin/menu-move', [...])` 走的是系统自带的 `file` 对象，只要 rpcd 在就能用；而且能把命令的 stderr 原样显示出来。`menu_move` ubus 对象仍然保留，供脚本/其它服务调用（`ubus call menu_move status|apply`）。
- **CI 每次都会打印包内文件清单**，装完可以对着确认 `menuMove.uc` 落在 `/usr/share/ucode/luci/` 下。
- 改包名只需改目录名 + `PKG_NAME`；`luci.mk` 用目录名推导 `LUCI_BASENAME`（这里是 `menuMove`），所以翻译包叫 `luci-i18n-menuMove-zh-cn`。

## 排错

先跑这四条，多半一眼就能定位（网页「状态」区显示的 `⚠` 报错就是第一条的输出）：

```sh
/usr/bin/menu-move status                    # 命令行能不能跑（报错原文就是根因）
ls -l /usr/share/ucode/luci/menuMove.uc /usr/share/rpcd/ucode/  # 文件是否就位
ubus list | grep menu_move                   # 可选的 ubus 对象是否注册
logread -e menu-move; logread -e rpcd        # 触发器/加载报错
```

| 现象 | 处理 |
|------|------|
| 状态区显示 `⚠ ...` 一行报错 | 那是 `/usr/bin/menu-move` 的原样输出：模块找不到 / 权限不对 / ucode 报语法错，都会写清楚。对照上面的 `ls -l` 看文件在不在 |
| 报 `Permission denied` | rpcd 的 ACL 没生效：确认 `/usr/share/rpcd/acl.d/luci-app-menuMove.json` 存在，然后 `/etc/init.d/rpcd reload` 再刷新 |
| 点「保存并应用」后菜单没变化 | 点「立即重新生成」，再硬刷新（Ctrl+Shift+R，浏览器会缓存菜单树）。`menu-move status` 的 `stale` 会提示覆盖文件是否过期 |
| 被隐藏的标签又出现了 | 看 `/usr/lib/luci-menu-move/.disabled` 是否存在（存在即解除隐藏）；或规则被禁用、总开关关了 |
| 原位置的标签还在 | 该规则没勾选「隐藏原位置的标签」；或这条菜单由老式 Lua controller 定义（命令行会警告） |
| 「刷新界面」后布局没变 | 浏览器缓存了菜单树：必须点本插件的「刷新界面」（会 `flushCache()`）或硬刷新，普通 F5 有时不够 |

## 许可

Apache-2.0（与 LuCI 一致）。

**ucode 模块导出写法（踩过的坑）**：本插件的 `menuMove.uc` 只用 `export function` 导出，
常量保持私有、通过 `menu_paths()` 返回。原因是设备上的 ucode（2026.01）**不支持 `export const`**
——它会报 `Unexpected token / Expecting ';'`，而且错误会连环报在后面几个语句边界上
（117/158/214…），非常难定位；顶层 `return { ... }` 也不行（`return must be inside function body`）。
`test/check.py` 第 9 节会守住这条规则。
