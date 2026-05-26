# Lab9：从访问控制到记录在案：防火墙的双重能力

## 实验背景

前面我们已经学过包结构、TCP 连接、防火墙规则和 NAT。但真实网络里，光会写几条规则还不够——有两个问题必须同时解决：

1. **分区**：主机本来就不应该混在一起。办公区、DMZ、访客区必须隔开。
2. **审计**：防火墙拦了流量，但你怎么知道有没有人在尝试跨区访问？如果没有日志，防火墙就是一个"哑巴保安"——能拦人，但不记录谁来过、什么时候来的、来了多少次。

本次实验把这两个问题**放在一起解决**，因为真实环境中的防火墙从来都是"分区 + 审计"一体化的。

实验分三个阶段，层层递进：

| 阶段 | 内容 | 核心问题 |
| :--- | :--- | :--- |
| 一 | 用 namespace 搭建网络分区，配上访问控制规则 | "谁能访问谁？" |
| 二 | 引入 LOG target，让每一次拦截都留下记录 | "谁在尝试访问谁？" |
| 三 | 用前缀过滤和速率限制管理日志 | "日志多了怎么办？" |

> **环境说明**：本实验使用 Linux `network namespace` 在一台机器上模拟 4 个区域（`office`、`guest`、`dmz`、`fw`）。
> `iptables -j LOG` 写入内核日志，用 `journalctl -k` 或 `dmesg` 读取。
> namespace 不隔离内核日志，所有 LOG 规则都写入同一份内核日志。

---

## 实验拓扑

```text
office (10.0.1.2) ----\
                       \
                        fw (10.0.1.1 / 10.0.2.1 / 10.0.3.1)
                       /  \
guest  (10.0.2.2) ---/    dmz (10.0.3.2)
```

**地址规划：**

| 区域 | 地址 |
| :--- | :--- |
| `office` | `10.0.1.2/24` |
| `fw` ↔ office | `10.0.1.1/24` |
| `guest` | `10.0.2.2/24` |
| `fw` ↔ guest | `10.0.2.1/24` |
| `dmz` | `10.0.3.2/24` |
| `fw` ↔ dmz | `10.0.3.1/24` |

**访问控制目标：**

1. `office` 可以访问 `dmz` 的 Web 服务（`10.0.3.2:8080`）
2. `guest` 不能访问 `dmz`
3. `guest` 不能访问 `office`

---

## 第一阶段：搭建网络分区

### 任务 1：创建 namespace 并组网

#### 1.1 创建 4 个 namespace

```bash
sudo ip netns add office
sudo ip netns add guest
sudo ip netns add dmz
sudo ip netns add fw
```

#### 1.2 创建 veth 对

```bash
sudo ip link add veth-office type veth peer name veth-fw-office
sudo ip link add veth-guest  type veth peer name veth-fw-guest
sudo ip link add veth-dmz    type veth peer name veth-fw-dmz
```

#### 1.3 将 veth 放入对应的 namespace

```bash
sudo ip link set veth-office    netns office
sudo ip link set veth-fw-office netns fw

sudo ip link set veth-guest     netns guest
sudo ip link set veth-fw-guest  netns fw

sudo ip link set veth-dmz       netns dmz
sudo ip link set veth-fw-dmz    netns fw
```

#### 1.4 配置 IP 地址并启用接口

```bash
# 配地址
sudo ip netns exec office ip addr add 10.0.1.2/24 dev veth-office
sudo ip netns exec fw     ip addr add 10.0.1.1/24 dev veth-fw-office
sudo ip netns exec guest  ip addr add 10.0.2.2/24 dev veth-guest
sudo ip netns exec fw     ip addr add 10.0.2.1/24 dev veth-fw-guest
sudo ip netns exec dmz    ip addr add 10.0.3.2/24 dev veth-dmz
sudo ip netns exec fw     ip addr add 10.0.3.1/24 dev veth-fw-dmz

# 启用 lo
sudo ip netns exec office ip link set lo up
sudo ip netns exec guest  ip link set lo up
sudo ip netns exec dmz    ip link set lo up
sudo ip netns exec fw     ip link set lo up

# 启用 veth
sudo ip netns exec office ip link set veth-office up
sudo ip netns exec guest  ip link set veth-guest up
sudo ip netns exec dmz    ip link set veth-dmz up
sudo ip netns exec fw     ip link set veth-fw-office up
sudo ip netns exec fw     ip link set veth-fw-guest  up
sudo ip netns exec fw     ip link set veth-fw-dmz    up
```

#### 1.5 配置默认路由

```bash
sudo ip netns exec office ip route add default via 10.0.1.1
sudo ip netns exec guest  ip route add default via 10.0.2.1
sudo ip netns exec dmz    ip route add default via 10.0.3.1
```

#### 1.6 在 fw 中开启 IP 转发

```bash
sudo ip netns exec fw sysctl -w net.ipv4.ip_forward=1
```

#### 1.7 验证连通性（此时还没有任何防火墙规则）

在 `dmz` 中启动 Web 服务：

```bash
sudo ip netns exec dmz python3 -m http.server 8080
```

在另一个终端测试：

```bash
# 这两条在配规则前都应该成功（因为还没有任何拦截）
sudo ip netns exec office curl --max-time 3 http://10.0.3.2:8080/
sudo ip netns exec guest  curl --max-time 3 http://10.0.3.2:8080/
```

> 保持 `dmz` 中的 `python3 -m http.server 8080` 持续运行，后续测试都要用到。

---

### 任务 2：配置访问控制规则（无日志版本）

现在在 `fw` 中设置 iptables 规则，实现访问控制目标。

```bash
sudo ip netns exec fw iptables -F
sudo ip netns exec fw iptables -P FORWARD DROP
sudo ip netns exec fw iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
sudo ip netns exec fw iptables -A FORWARD -s 10.0.1.0/24 -d 10.0.3.2 -p tcp --dport 8080 -j ACCEPT
sudo ip netns exec fw iptables -A FORWARD -s 10.0.2.0/24 -d 10.0.3.2 -p tcp --dport 8080 -j REJECT
sudo ip netns exec fw iptables -A FORWARD -s 10.0.2.0/24 -d 10.0.1.0/24 -j REJECT
```

查看规则：

```bash
sudo ip netns exec fw iptables -L FORWARD -n -v --line-numbers
```

你应该看到：

```text
num  target    prot opt source          destination
1    ACCEPT    all  --  0.0.0.0/0       0.0.0.0/0       ctstate RELATED,ESTABLISHED
2    ACCEPT    tcp  --  10.0.1.0/24     10.0.3.2        tcp dpt:8080
3    REJECT    tcp  --  10.0.2.0/24     10.0.3.2        tcp dpt:8080
4    REJECT    all  --  10.0.2.0/24     10.0.1.0/24
```

**规则解读（很重要，请理解后再继续）：**

| 行号 | 作用 | 为什么放在这个位置 |
| :--- | :--- | :--- |
| 1 | 允许已建立连接的回包通过 | 否则 TCP 三次握手永远无法完成 |
| 2 | 放行 `office -> dmz:8080` | 白名单：合法业务流量 |
| 3 | 拒绝 `guest -> dmz:8080` | 访客不能访问内部服务 |
| 4 | 拒绝 `guest -> office` | 访客不能接触办公网 |
| 默认 | 其余全部丢弃（`-P FORWARD DROP`） | 白名单模型：只放已知合法流量 |

---

### 任务 3：测试访问控制

#### 3.1 测试 `office -> dmz`（应该成功）

```bash
sudo ip netns exec office curl --max-time 3 http://10.0.3.2:8080/
```

预期：返回 HTML 目录列表。

#### 3.2 测试 `guest -> dmz`（应该被拒绝）

```bash
sudo ip netns exec guest curl --max-time 3 http://10.0.3.2:8080/
```

预期：`Connection refused` 或超时。

#### 3.3 测试 `guest -> office`（应该被拒绝）

```bash
sudo ip netns exec guest curl --max-time 3 http://10.0.1.2:8080/
```

预期：连接失败。

**填写下表：**

| 测试项 | 结果（成功/失败） | 现象描述 |
| :----- | :-------------- | :------- |
| `office -> dmz:8080` |成功|返回了完整的 HTML 目录列表（包含文件列表的网页内容）|
| `guest -> dmz:8080` |	失败|curl 报错：Failed to connect to 10.0.3.2 port 8080: Could not connect to server，连接被拒绝 |
| `guest -> office` |	失败|curl 报错：Failed to connect to 10.0.1.2 port 8080: Could not connect to server，连接失败 |

**第一阶段小结**：现在防火墙在正常工作——该放的放，该拦的拦。你可以截图 `topology.png`（地址与连通性）和 `baseline_rules.png`（规则 + 测试结果），然后我们进入第二阶段。

---

## 第二阶段：让防火墙"开口说话"

第一阶段做完了，但你发现一个问题没有？

> `guest` 访问 `dmz` 被拒绝了——但**你拿不出任何证据**证明"有人尝试过"。

如果是真实网络，安全团队需要知道：
- 谁在尝试跨区访问？（源 IP）
- 想访问什么服务？（目的端口）
- 什么时候发生的？（时间戳）
- 发生了多少次？（频次）

没有日志，这些问题全部无法回答。接下来我们给防火墙加上"眼睛"。

---

### 新工具：iptables LOG target 与内核日志

#### LOG target 是什么

`LOG` 是 iptables 的一个特殊 target。与 `ACCEPT`、`DROP`、`REJECT` 不同，**LOG 不终止规则匹配**——它把命中的包的信息写入内核日志，然后继续向后匹配下一条规则。

这意味着 LOG 必须和 DROP 或 REJECT **成对出现**：

```text
规则 N：   匹配条件 → LOG     （记录）
规则 N+1： 匹配条件 → REJECT  （拦截）
```

同一个包先命中 LOG 留下记录，再命中 REJECT 被拒绝。

#### --log-prefix：给日志打标签

```bash
iptables ... -j LOG --log-prefix "GUEST-TO-DMZ: "
```

`--log-prefix` 在日志行开头加一段固定前缀，最长 29 个字符（含末尾空格）。有了前缀，就能用 `grep` 精确过滤出某类事件。

#### 读取内核日志

`iptables -j LOG` 写入的是 Linux **内核日志**。两种常用读法：

| 命令 | 适用场景 | 关键参数 |
| :--- | :--- | :--- |
| `sudo journalctl -k -f` | systemd 系统（推荐） | `-k`=只看内核日志，`-f`=实时追踪 |
| `sudo dmesg -w` | 无 systemd 或更简单环境 | `-w`=实时显示新消息 |

加过滤：

```bash
sudo journalctl -k -f --grep "GUEST-TO-DMZ"    # journalctl 方式
sudo dmesg -w | grep "GUEST-TO-DMZ"            # dmesg 方式
```

#### 一行日志长什么样

```text
[12345.678901] GUEST-TO-DMZ: IN=veth-fw-guest OUT=veth-fw-dmz
  MAC=... SRC=10.0.2.2 DST=10.0.3.2 LEN=60 TOS=0x00
  PROTO=TCP SPT=54321 DPT=8080 FLAGS=SYN
```

| 字段 | 含义 |
| :--- | :--- |
| `[12345.678]` | 系统启动后的秒数（内核时间戳） |
| `GUEST-TO-DMZ:` | 你设置的 `--log-prefix` |
| `IN=` | 包从哪个接口进入 |
| `OUT=` | 包将从哪个接口转发出去 |
| `MAC=` | 二层头信息，通常能看到源/目的 MAC；排查链路方向时有帮助 |
| `SRC=` | 源 IP 地址 |
| `DST=` | 目的 IP 地址 |
| `LEN=` | IP 包总长度（字节） |
| `TOS=` | Type of Service / DSCP 相关字段，表示服务质量标记 |
| `PREC=` | 旧式优先级字段，现代环境中通常是 `0x00` |
| `TTL=` | 生存时间，每经过一跳会减 1，可辅助判断路径长度 |
| `ID=` | IP 分片标识，同一报文分片后通常共享同一个 ID |
| `DF` | Don't Fragment 标志，表示该包不允许被分片 |
| `PROTO=` | 协议（TCP / UDP / ICMP） |
| `SPT=` | 源端口 |
| `DPT=` | 目的端口 |
| `WINDOW=` | TCP 窗口大小，表示接收端当前可接受的数据量 |
| `RES=` | TCP 头中的保留位，通常为 `0x00` |
| `SYN` / `ACK` / `FIN` / `RST` | TCP 标志位；`SYN` 常表示新连接请求，`RST` 常表示异常中断或拒绝 |
| `URGP=` | TCP 紧急指针，通常为 0 |

看到一行日志时，最值得先读的通常是这几组字段：

1. `GUEST-TO-DMZ:`：这是谁触发的哪一类规则。
2. `IN=` 和 `OUT=`：包是从哪个区域进、准备往哪个区域去。
3. `SRC=` 和 `DST=`：是谁访问谁。
4. `PROTO=`、`SPT=`、`DPT=`：访问的是哪种协议、从哪个源端口发起、目标服务端口是多少。
5. `SYN`/`ACK`/`RST`：这是新连接、已建立连接中的报文，还是异常/拒绝相关报文。

---

### 任务 4：为拦截规则加上 LOG

现在把第一阶段中两条 REJECT 规则改造成"先 LOG，再 REJECT"的形式。

#### 4.1 清空现有规则，保留默认策略

```bash
sudo ip netns exec fw iptables -F
sudo ip netns exec fw iptables -P FORWARD DROP
```

#### 4.2 加回放行规则（不变）

```bash
sudo ip netns exec fw iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
sudo ip netns exec fw iptables -A FORWARD -s 10.0.1.0/24 -d 10.0.3.2 -p tcp --dport 8080 -j ACCEPT
```

#### 4.3 将 `guest -> dmz` 改造成 LOG + REJECT

```bash
# 先 LOG
sudo ip netns exec fw iptables -A FORWARD \
  -s 10.0.2.0/24 -d 10.0.3.2 -p tcp --dport 8080 \
  -j LOG --log-prefix "GUEST-TO-DMZ: " --log-level 4

# 再 REJECT
sudo ip netns exec fw iptables -A FORWARD \
  -s 10.0.2.0/24 -d 10.0.3.2 -p tcp --dport 8080 \
  -j REJECT
```

**关键点**：LOG 和 REJECT 的匹配条件完全相同，LOG 必须排在 REJECT 前面。

#### 4.4 将 `guest -> office` 也改造成 LOG + REJECT

```bash
sudo ip netns exec fw iptables -A FORWARD \
  -s 10.0.2.0/24 -d 10.0.1.0/24 \
  -j LOG --log-prefix "GUEST-TO-OFFICE: " --log-level 4

sudo ip netns exec fw iptables -A FORWARD \
  -s 10.0.2.0/24 -d 10.0.1.0/24 \
  -j REJECT
```

#### 4.5 查看完整规则

```bash
sudo ip netns exec fw iptables -L FORWARD -n -v --line-numbers
```

预期输出：

```text
num  target    prot opt source          destination
1    ACCEPT    all  --  0.0.0.0/0       0.0.0.0/0       ctstate RELATED,ESTABLISHED
2    ACCEPT    tcp  --  10.0.1.0/24     10.0.3.2        tcp dpt:8080
3    LOG       tcp  --  10.0.2.0/24     10.0.3.2        tcp dpt:8080  LOG flags 0 ... prefix "GUEST-TO-DMZ: "
4    REJECT    tcp  --  10.0.2.0/24     10.0.3.2        tcp dpt:8080  reject-with icmp-port-unreachable
5    LOG       all  --  10.0.2.0/24     10.0.1.0/24     LOG flags 0 ... prefix "GUEST-TO-OFFICE: "
6    REJECT    all  --  10.0.2.0/24     10.0.1.0/24     reject-with icmp-port-unreachable
```

**填写下表：**

| 项目 | 你的填写 |
| :--- | :------- |
| LOG 规则条数 |2 |
| REJECT 规则条数 |2 |
| `GUEST-TO-DMZ:` LOG 位于第几行 |第 3 行 |
| 对应的 REJECT 位于第几行 |第 4 行 |
| LOG 与 REJECT 的匹配条件是否完全一致 |是 |

**简答：**

1. 如果把 LOG 放在 REJECT 之后，LOG 规则还会被命中吗？为什么？

> 答：不会被命中。原因如下：（1）iptables 的规则是从上到下依次匹配的，一旦某条规则匹配成功并执行了终止动作（如 REJECT、DROP），数据包就会被直接处理，不会再继续匹配后面的规则。（2）如果把 LOG 规则放在 REJECT 之后，当数据包匹配到 REJECT 规则时，会被直接拒绝 / 丢弃，流程就结束了，根本不会走到后面的 LOG 规则，因此日志不会被记录。


---

### 任务 5：触发日志并实时观察

#### 5.0 如果规则命中了但看不到日志，先处理这个常见问题

在 Ubuntu 24.04 这类使用 `iptables-nft` 的环境中，如果你已经确认 `LOG` 规则命中计数在增长，但 `journalctl -k` 和 `dmesg` 仍然看不到任何 `GUEST-TO-*` 日志，通常是因为内核默认不记录来自非初始 network namespace 的 netfilter LOG。

先在**宿主机**执行：

```bash
sudo sysctl -w net.netfilter.nf_log_all_netns=1
```

然后重新触发一次 `guest` 的违规访问，再观察日志。这个开关要在宿主机设置，不是在 `fw` namespace 里设置。

#### 5.1 打开日志监视（终端 D）

```bash
# 推荐
sudo journalctl -k -f

# 或
sudo dmesg -w
```

保持这个终端持续运行，切换到另一个终端（终端 B）触发访问。

#### 5.2 触发 guest 的违规访问

在终端 B 分别执行：

```bash
# guest -> dmz（会被 REJECT，预期产生 LOG）
sudo ip netns exec guest curl --max-time 3 http://10.0.3.2:8080/

# guest -> office（会被 REJECT，预期产生 LOG）
sudo ip netns exec guest curl --max-time 3 http://10.0.1.2:8080/

# office -> dmz（会被 ACCEPT，预期不产生 LOG）
sudo ip netns exec office curl --max-time 3 http://10.0.3.2:8080/
```

每次执行后立刻切回终端 D，观察内核日志的变化。

#### 5.3 填写下表

| 测试 | 访问结果 | 是否出现 LOG | 日志前缀 |
| :--- | :------- | :---------- | :------- |
| `guest -> dmz:8080` |失败 |是 |GUEST-TO-DMZ: |
| `guest -> office` |失败 |是 | GUEST-TO-OFFICE:|
| `office -> dmz:8080` |成功| 否| 无|

**从终端 D 复制一条实际日志行：**
May 21 09:01:19 LAPTOP-OJ3UTDIO kernel: GUEST-TO-DMZ: IN=veth-fw-guest OUT=veth-fw-dmz MAC=5e:cb:62:fc:04:05:66:14:e4:44:08:bb:08:00 SRC=10.0.2.2 DST=10.0.3.2 LEN=60 TOS=0x00 PREC=0x00 TTL=63 ID=55808 DF PROTO=TCP SPT=58048 DPT=8080 WINDOW=64240 RES=0x00 SYN URGP=0
```text

```

**简答：**

1. `office -> dmz` 成功了但没有日志，这合理吗？如果想记录"所有被放行的流量"，应该怎么做？

> 答：这是合理的。因为你配置的 LOG 规则只针对 guest 访问 dmz 和 office 的流量，office -> dmz 的流量匹配的是 ACCEPT 规则，没有对应的 LOG 规则，所以不会产生日志。如果想记录所有被放行的流量，需要在 ACCEPT 规则之前，为这些流量添加对应的 LOG 规则

2. 从日志里你能读出什么信息？"谁在访问谁的什么服务"能还原出来吗？

> 答：可以还原出来，以示例日志为例：
谁在访问：SRC=10.0.2.2，这是 guest 命名空间的主机 IP。
访问谁：DST=10.0.3.2，这是 dmz 命名空间的主机 IP。
什么服务：PROTO=TCP DPT=8080，说明是访问目标主机的 8080 端口的 TCP 服务（即你启动的 HTTP 服务）。
其他信息还包括：接口信息（IN=veth-fw-guest、OUT=veth-fw-dmz）、TCP 标志位（SYN，表示是连接请求包）等。

---

### 任务 6：用前缀过滤日志

内核日志里不只有 iptables 的消息，还有各种驱动、硬件事件。如果不过滤，想在嘈杂的输出中找到防火墙日志会很困难。

#### 6.1 按前缀精确过滤

```bash
# 只看 GUEST-TO-DMZ 事件
sudo journalctl -k --grep "GUEST-TO-DMZ" --no-pager

# dmesg 方式
sudo dmesg | grep "GUEST-TO-DMZ"
```

#### 6.2 按前缀模糊匹配

```bash
# 同时看 GUEST-TO-DMZ 和 GUEST-TO-OFFICE（共前缀）
sudo journalctl -k --grep "GUEST-TO" --no-pager
```

#### 6.3 统计每类事件各发生多少次

```bash
sudo journalctl -k --grep "GUEST-TO-DMZ" --no-pager    | wc -l
sudo journalctl -k --grep "GUEST-TO-OFFICE" --no-pager | wc -l
```

#### 6.4 循环多次触发，验证计数增长

```bash
for i in $(seq 1 5); do
  sudo ip netns exec guest curl --max-time 2 http://10.0.3.2:8080/ 2>/dev/null
done
```

再次统计，确认条数增加了 5。

**填写下表：**

| 项目 | 你的填写 |
| :--- | :------- |
| `GUEST-TO-DMZ` 总日志条数 | 6|
| `GUEST-TO-OFFICE` 总日志条数 |1 |
| 循环 5 次后 `GUEST-TO-DMZ` 增加了多少 |5 |
| 你使用的过滤命令 |sudo journalctl -k --grep "GUEST-TO-DMZ" --no-pager |

**简答：**

1. `--log-prefix` + `grep` 组合，相当于给不同事件打了不同"标签"。在真实场景中这有什么实际价值？

> 答：（1）快速分类与定位问题：通过不同的日志前缀（如 GUEST-TO-DMZ、OFFICE-TO-INET），可以直接用 grep 过滤出特定类型的访问日志，快速定位异常流量来源、被访问的目标和服务，大幅缩短故障排查时间。
（2）流量审计与合规：不同前缀可以清晰区分不同业务 / 区域的访问行为，方便进行安全审计、合规检查，例如统计访客网络对 DMZ 区的非法访问次数。
（3）日志可视化与告警：日志分析系统（如 ELK）可以基于这些前缀进行索引和聚合，实现按事件类型的监控、告警和报表统计，提升安全运营效率。

2. 如果防火墙每秒拦截上千个包，不加限制地写 LOG 会有什么后果？

> 答：（1）日志风暴与磁盘占用：大量拦截包会产生海量日志，短时间内快速占满服务器磁盘空间，甚至导致系统日志服务崩溃，影响系统稳定性。
（2）系统性能下降：内核频繁写入日志会占用大量 CPU 和 IO 资源，加重防火墙设备的负载，导致正常业务流量的转发性能下降，甚至出现丢包。
（3）日志可读性下降：大量重复的日志会淹没关键异常信息，反而不利于故障排查和安全分析。
（4）安全风险：日志中可能包含源 IP、目标 IP、端口等敏感信息，过多的日志也会增加信息泄露的风险。

---

## 第三阶段：日志速率控制

### 任务 7：用 --limit 限制日志写入频率

如果每秒有大量同类包被拦截（比如遭遇扫描攻击），不加限制的 LOG 会让日志系统不堪重负，甚至影响系统性能。`-m limit` 模块可以限制每条规则的日志写入频率。

#### 7.1 删除原来的 `GUEST-TO-DMZ` LOG 规则

```bash
# 先查行号
sudo ip netns exec fw iptables -L FORWARD -n --line-numbers

# 按行号删除 GUEST-TO-DMZ 的 LOG 规则
sudo ip netns exec fw iptables -D FORWARD <行号>
```

#### 7.2 插入带速率限制的 LOG 规则

```bash
sudo ip netns exec fw iptables -I FORWARD 3 \
  -s 10.0.2.0/24 -d 10.0.3.2 -p tcp --dport 8080 \
  -m limit --limit 3/min --limit-burst 5 \
  -j LOG --log-prefix "GUEST-TO-DMZ: " --log-level 4
```

| 参数 | 含义 |
| :--- | :--- |
| `-I FORWARD 3` | 插入到第 3 行，保持在 REJECT 规则之前 |
| `-m limit` | 使用 `limit` 模块进行速率匹配 |
| `--limit 3/min` | 稳定状态下每分钟最多写 3 条日志 |
| `--limit-burst 5` | 允许初始突发最多 5 条，之后降到 `--limit` 速率 |

#### 7.3 快速触发 10 次访问

```bash
# 先记下本轮测试开始时间，避免把历史日志也算进去
start=$(date '+%Y-%m-%d %H:%M:%S')

for i in $(seq 1 10); do
  sudo ip netns exec guest curl --max-time 1 http://10.0.3.2:8080/ 2>/dev/null
done
```

#### 7.4 统计实际产生的日志条数

```bash
# 只统计“本轮测试开始以后”新增的日志
sudo journalctl -k --since "$start" --grep "GUEST-TO-DMZ" --no-pager | wc -l

# 同时看规则计数，直观看到“10 次访问都被 REJECT，但 LOG 只记了少数几次”
sudo ip netns exec fw iptables -L FORWARD -n -v --line-numbers
```

**关键观察**：

1. `guest -> dmz` 的 10 次访问仍然都会失败，说明后面的 `REJECT` 对每次访问都生效。
2. `journalctl --since "$start" ... | wc -l` 统计到的新增日志条数不超过 `--limit-burst` 的值（5 条）。
3. `iptables -L FORWARD -n -v` 中第 4 条 `REJECT` 的命中次数会明显大于第 3 条 `LOG` 的命中次数，这就是 `limit` 生效的直接证据。

如果你想把这个现象看得更清楚，可以连续执行两次下面的对照命令：

```bash
sudo journalctl -k --since "$start" --grep "GUEST-TO-DMZ" --no-pager
sudo ip netns exec fw iptables -L FORWARD -n -v --line-numbers
```

你应该能看到：日志行数最多只有 5 条，但 `REJECT` 规则已经处理了 10 次访问。

#### 7.5 填写下表

| 项目 | 你的填写 |
| :--- | :------- |
| 触发访问次数 | 10 |
| 实际写入日志条数 |5 |
| `--limit-burst 5` 的含义 |初始突发访问时，最多允许记录 5 条日志，之后进入稳定限速状态 |
| 未记录的访问是否仍然被 REJECT |是 |

**简答：**

1. `--limit` 只控制了 LOG 的写入频率，但后面的 REJECT 仍对每个包生效。这说明 LOG 和 REJECT 在逻辑上是什么关系？

> 答：LOG 和 REJECT 是独立的两个动作，彼此互不影响：LOG 仅负责 “匹配并记录日志”，它不会改变数据包的流向，也不会终止规则链的匹配过程。REJECT 负责 “匹配并拒绝数据包”，只要数据包命中这条规则，就会被直接拦截，与前面的 LOG 是否记录日志无关。因此，即使 LOG 被 --limit 限制只记录部分包，REJECT 依然会对所有匹配的包生效。

2. "没记录就当没发生"和"仍然拦截但不记录"有本质区别吗？为什么安全审计中这个区别很重要？

> 答：（1）两者有本质区别："没记录就当没发生"：代表攻击 / 违规访问既没有被记录，也没有被拦截，攻击者可以成功访问目标服务，安全事件完全逃逸了监控和防护。"仍然拦截但不记录"：代表攻击 / 违规访问已经被防火墙成功拦截，只是因为日志限速等原因没有全部记录下来，防护动作是有效的。
（2）在安全审计中，这个区别至关重要：
防护有效性验证：审计的核心是确认防护规则是否真正生效，而不是只看日志是否记录。如果只看日志条数，会误判为 “没有攻击”，但实际攻击一直在被拦截。
日志风暴防护：限速日志是防止磁盘被恶意占满的重要手段，不能因为日志条数少就否定防护效果。
事件溯源与分析：只要有部分日志记录，就可以还原攻击模式和来源，而完全没有日志的逃逸事件则无法被追溯，是严重的安全漏洞。

---

### 任务 8：清理

实验结束后清理所有 namespace：

```bash
sudo ip netns del office
sudo ip netns del guest
sudo ip netns del dmz
sudo ip netns del fw

# 确认清理完毕
sudo ip netns list
```

输出为空即完成。

---

## 实验结果填写

### A. 环境搭建

| 项目 | 你的填写 |
| :--- | :------- |
| `office` 地址 |10.0.1.2/24 |
| `guest` 地址 |10.0.2.2/24 |
| `dmz` 地址 |10.0.3.2/24 |
| `fw` 三个接口地址 |10.0.1.1/24、10.0.2.1/24、10.0.3.1/24 |

![环境配置](topology.png)

---

### B. 第一阶段：访问控制规则（无日志）

| 规则作用 | 你的规则 |
| :------- | :------- |
| 默认转发策略 | DROP|
| 允许已建立连接返回 |iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT |
| 允许 `office -> dmz:8080` | 	iptables -A FORWARD -s 10.0.1.0/24 -d 10.0.3.2 -p tcp --dport 8080 -j ACCEPT|
| 拒绝 `guest -> dmz:8080` | iptables -A FORWARD -s 10.0.2.0/24 -d 10.0.3.2 -p tcp --dport 8080 -j REJECT|
| 拒绝 `guest -> office` |iptables -A FORWARD -s 10.0.2.0/24 -d 10.0.1.0/24 -j REJECT |

| 测试项 | 结果 |
| :----- | :--- |
| `office -> dmz:8080` |成功 |
| `guest -> dmz:8080` | 失败|
| `guest -> office` |失败 |

![访问控制测试](baseline_test.png)

---

### C. 第二阶段：带日志的规则

| 规则行号 | target | 匹配条件 | 前缀（如有） |
| :------- | :----- | :------- | :---------- |
| 1 |ACCEPT |0.0.0.0/0 → 0.0.0.0/0，ctstate RELATED,ESTABLISHED |无 |
| 2 | ACCEPT|10.0.1.0/24 → 10.0.3.2，tcp dpt:8080 | 无 |
| 3 | LOG|10.0.2.0/24 → 10.0.3.2，tcp dpt:8080 | 	GUEST-TO-DMZ:|
| 4 |REJECT |10.0.2.0/24 → 10.0.3.2，tcp dpt:8080 |无  |
| 5 | LOG|10.0.2.0/24 → 10.0.1.0/24，all | GUEST-TO-OFFICE:|
| 6 |REJECT |10.0.2.0/24 → 10.0.1.0/24，all |无|

| 测试 | 访问结果 | 是否出现 LOG | 日志前缀 |
| :--- | :------- | :---------- | :------- |
| `guest -> dmz:8080` |失败 | 是|GUEST-TO-DMZ: |
| `guest -> office` |失败 |是 |GUEST-TO-OFFICE: |
| `office -> dmz:8080` |成功 |否 |无 |

![带日志的规则](log_rules.png)

![实时日志](realtime_log.png)

---

### D. 日志过滤与速率控制

| 项目 | 你的填写 |
| :--- | :------- |
| `GUEST-TO-DMZ` 总条数 |11 |
| `GUEST-TO-OFFICE` 总条数 | 1|
| 过滤命令 | sudo journalctl -k --grep "GUEST-TO-DMZ" --no-pager|
| 加 `--limit` 后触发 10 次，日志条数 |5 |

![日志过滤](log_filter.png)

![速率限制](log_limit.png)

---

## 思考题

1. 为什么 `dmz` 的服务不应该和 `office` 混在同一个网段？用本实验的规则效果来说明。

   > 答：DMZ 区（非军事区）存放对外提供服务的设备，而 Office 区（办公内网）存放敏感业务数据，两者安全等级完全不同。在本实验中，我们可以配置规则：仅允许 office -> dmz 的主动访问，同时完全禁止 dmz -> office 的主动访问，实现单向访问控制。如果两者混在同一网段，防火墙无法基于 IP 段进行细粒度隔离，一旦 DMZ 区的服务被攻破，攻击者可以直接横向移动到同一网段的办公主机，造成数据泄露。而分网段部署时，即使 DMZ 区被攻陷，也无法通过防火墙访问到办公网。


2. 为什么 `guest` 一般应该和办公网隔离？

   > 答：Guest 网络（访客网络）是为外来用户提供互联网接入的开放区域，安全风险极高：（1）访问权限不受控：访客设备的安全状态未知，可能携带恶意软件或被入侵，若与办公网连通，会直接威胁内部主机安全。（2）流量行为不可信：访客流量可能包含攻击、扫描等恶意行为，隔离后可通过防火墙对其进行严格管控，例如本实验中拒绝 guest -> office 的所有访问。（3）合规要求：办公网通常存储敏感数据，与访客网络隔离是基本的安全合规措施，防止数据被非法访问或窃取。


3. 第一阶段中为什么必须保留 `ESTABLISHED,RELATED` 规则？如果去掉会怎样？

   > 答：ESTABLISHED,RELATED 规则用于允许已建立或相关的连接通过防火墙：（1）当 office 主动访问 dmz 的服务时，会发起一个 TCP 连接，后续服务端返回的响应包属于ESTABLISHED状态。（2）如果去掉这条规则，响应包会被默认的 DROP 策略拒绝，导致 office 无法收到服务返回的数据，表现为 “能发起请求但无法获取响应”，业务完全无法正常使用。


4. `LOG` target 和 `ACCEPT`/`DROP`/`REJECT` 最本质的区别是什么？为什么 LOG 必须放在 REJECT 前面？

   > 答：（1）本质区别：LOG 仅用于记录数据包日志，不会改变数据包的流向，也不会终止规则链匹配；而 ACCEPT/DROP/REJECT 会直接决定数据包的最终命运（放行 / 丢弃 / 拒绝），并终止后续规则匹配。（2）LOG 必须放在 REJECT 前面的原因：如果 REJECT 在前，数据包命中后会被直接拒绝，规则链匹配终止，后面的 LOG 规则永远不会被命中，导致无法记录被拒绝的访问日志。

5. `office -> dmz` 的成功访问没有日志，被拦截的 `guest` 访问有日志。这是合理的还是疏漏？为什么？

   > 答：这是合理的设计，而非疏漏：（1）日志会消耗系统资源和磁盘空间，对于频繁的正常业务访问（如 office -> dmz），默认不记录日志可以避免日志风暴。（2）被拦截的访问属于异常 / 违规行为，记录日志便于安全审计和溯源，是安全运维的常规做法。（3）如果需要记录成功访问，也可以为 ACCEPT 规则添加对应的 LOG 规则，但会增加系统开销，需根据实际需求决定。

6. `--limit` 控制日志频率后，超出限制的包仍然被 REJECT 但不再写入日志。这和"只要不记录就当没发生"有本质区别吗？

   > 答：两者有本质区别：（1）--limit 仅限制日志写入频率，不影响 REJECT 规则的执行，所有违规包依然会被防火墙拦截，防护动作是有效的，只是日志没有全部记录。（2）"只要不记录就当没发生" 意味着既没有记录也没有拦截，攻击者可以成功访问目标，安全事件完全逃逸了监控和防护，是严重的安全漏洞。

7. 如果后面要接入 VPN，你认为 VPN 用户应该先接入哪个区域？结合网络分区和日志审计两个角度回答。

   > 答：VPN 用户应先接入DMZ 区，而非直接接入 Office 办公网：（1）网络分区角度：DMZ 区是安全等级介于公网和内网之间的缓冲区域，VPN 接入设备（如 VPN 网关）部署在 DMZ 区，可通过防火墙限制 VPN 用户仅能访问授权的内网资源，避免直接暴露整个办公网。（2）日志审计角度：VPN 用户的所有访问行为都可以通过 DMZ 区的防火墙 / 网关进行日志记录，便于后续审计和溯源，若直接接入办公网，日志粒度和管控难度会大幅提升。


---

## 截图要求

- 截图须清晰，终端文字可读。
- 所有截图与本 `Lab9.md` 放在**同一目录**下。

| 截图内容 | 文件名 | 对应阶段 |
| :------- | :----- | :------- |
| 地址与接口配置、连通性验证 | `topology.png` | 任务 1 |
| 无日志版规则 + 三种访问测试结果 | `baseline_test.png` | 任务 2-3 |
| 带 LOG 的完整规则列表 | `log_rules.png` | 任务 4 |
| 实时日志观察（含完整 LOG 行） | `realtime_log.png` | 任务 5 |
| 日志过滤与统计（grep + wc -l） | `log_filter.png` | 任务 6 |
| 速率限制效果（10 次触发，日志远少于 10） | `log_limit.png` | 任务 7 |

**各截图具体要求：**

1. `topology.png`：能看到 namespace 列表、地址配置或 `ip route` 输出。
2. `baseline_test.png`：能看到规则列表 + 三种访问的测试结果（无需 LOG）。
3. `log_rules.png`：能看到 LOG 与 REJECT 成对出现，前缀清晰可辨。
4. `realtime_log.png`：能看到至少一条完整 LOG 行，含 `SRC=`、`DST=`、`DPT=` 等字段。
5. `log_filter.png`：能看到 `grep`/`--grep` 过滤命令及其输出，以及 `wc -l` 统计结果。
6. `log_limit.png`：能看到触发 10 次但日志条数不超过 `--limit-burst`（5 条）的现象。

---

## 提交要求

在自己的文件夹下新建 `Lab9/` 目录，提交以下文件：

```text
学号姓名/
└── Lab9/
    ├── Lab9.md          # 本文件（填写完整，含截图与答案）
    ├── topology.png
    ├── baseline_test.png
    ├── log_rules.png
    ├── realtime_log.png
    ├── log_filter.png
    └── log_limit.png
```

---

## 截止时间

2026-05-28，届时关于 Lab9 的 PR 将不会被合并。

