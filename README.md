<h1 align="center">Spug</h1>

<div align="center">

Spug是面向中小型企业设计的轻量级无Agent的自动化运维平台，整合了主机管理、主机批量执行、主机在线终端、应用发布部署、在线任务计划、配置中心、监控、报警等一系列功能。

</div>

## 二次开发版本

当前仓库正在建设私有化智能运维平台，开发中的预发布版本为 `ops-v0.1.0-alpha.12`（基于 OpenSpug v3.4.0 二次开发）。现已完成资产凭据/身份/对象授权、高风险操作审批与防篡改审计；SFTP 支持分片续传、批量队列、校验、Range 下载和受审批在线编辑；RDP/VNC 通过 Apache Guacamole 接入短时一次性票据与二次授权；Prometheus/Alertmanager 提供主机指标、规则告警、恢复、认领和静默闭环；知识库提供空间角色、文档审批、不可覆盖版本和授权检索；AI 运维默认关闭，仅基于当前用户授权证据进行带引用的只读调查，不携带执行工具，管理员可在 AI 页面加密配置模型地址和密钥。远程会话真实状态回调/强制断开/录像、任务证据和配置中心统一授权仍待后续完善。

- [建设蓝图](docs/OPS_PLATFORM_BLUEPRINT.md)
- [M1 资产与授权说明](docs/M1_ASSET_ACCESS.md)
- [M1 审批与审计说明](docs/M1_APPROVAL_AUDIT.md)
- [M2 SFTP 续传与清理说明](docs/M2_RESUMABLE_SFTP.md)
- [M3 RDP/VNC 安全接入](docs/M3_REMOTE_DESKTOP.md)
- [M4 主机监控与告警闭环](docs/M4_OBSERVABILITY.md)
- [M5 知识库与只读 AI 运维](docs/M5_KNOWLEDGE_AIOPS.md)
- [安全验证基线](docs/SECURITY_BASELINE.md)
- [私有化 Docker 部署](docs/docker/README.md)
- [版本记录](CHANGELOG.md)

该预发布版本受旧技术栈漏洞阻断，只能用于隔离内网验证，不应直接暴露公网或作为正式生产版本。

- 公司官网：https://www.spug.cc
- 项目官网：https://ops.spug.cc
- 使用文档：https://ops.spug.cc/docs/about-spug/

## 演示环境

演示地址：https://demo.spug.cc

## 🔐免费通配符SSL证书
免费通配符，付费证书价格亲民，性价比超高，低于市场其他平台价格，免费专家一对一配置服务，购买流程简单快速，且支持7天无理由退款和开具发票。提供一键下载和SSL过期通知配置，免费申请：[https://ssl.spug.cc](https://ssl.spug.cc)


## 🔥短信/电话/邮件推送

推送助手是一个集成了电话、短信、邮件、飞书、钉钉、微信、企业微信等多通道的消息推送平台，可以3分钟实现电话/短信/邮件消息的推送，点击体验：[https://sms.spug.cc](https://sms.spug.cc/guide/send-sms)


## 演示环境

演示地址：https://demo.spug.cc


## 特性

- **批量执行**: 主机命令在线批量执行
- **在线终端**: 主机支持浏览器在线终端登录
- **文件管理**: 主机文件在线上传下载
- **任务计划**: 灵活的在线任务计划
- **发布部署**: 支持自定义发布部署流程
- **配置中心**: 支持KV、文本、json等格式的配置
- **监控中心**: 支持站点、端口、进程、自定义等监控
- **报警中心**: 支持短信、邮件、钉钉、微信等报警方式
- **优雅美观**: 基于 Ant Design 的UI界面
- **开源免费**: 前后端代码完全开源


## 环境

* Python 3.8+
* Django 2.2
* Node 12.14
* React 16.11

## 安装

[官方文档](https://ops.spug.cc/docs/install-docker)

更多使用帮助请参考： [使用文档](https://ops.spug.cc/docs/host-manage/)


## 推荐项目
[Yearning — MYSQL 开源SQL语句审核平台](https://github.com/cookieY/Yearning)


## 预览

### 主机管理
![image](https://cdn.spug.cc/img/3.0/host.jpg)

#### 主机在线终端
![image](https://cdn.spug.cc/img/3.0/web-terminal.jpg)

#### 文件在线上传下载
![image](https://cdn.spug.cc/img/3.0/file-manager.jpg)

#### 主机批量执行
![image](https://cdn.spug.cc/img/3.0/host-exec.jpg)
![image](https://cdn.spug.cc/img/3.0/host-exec2.jpg)

#### 应用发布
![image](https://cdn.spug.cc/img/3.0/deploy.jpg)

#### 监控报警
![image](https://cdn.spug.cc/img/3.0/monitor.jpg)

#### 角色权限
![image](https://cdn.spug.cc/img/3.0/user-role.jpg)


## 赞助
<table>
  <thead>
    <tr>
      <th align="center" style="width: 115px;">
        <a href="https://www.ucloud.cn/site/active/kuaijie.html?invitation_code=C1xD0E5678FBA77">
          <img src="https://cdn.spug.cc/img/ucloud.png" width="115px"><br>
          <sub>UCloud</sub><br>
          <sub>5 元/月云主机</sub>
        </a>
      </th>
        <th align="center" style="width: 115px;">
        <a href="https://www.aliyun.com/minisite/goods?userCode=bkj6b9tn">
          <img src="https://cdn.spug.cc/img/aliyun-logo.png" width="115px"><br>
          <sub>阿里云</sub><br>
          <sub>2核心2G低至99元/年</sub>
        </a>
      </th>
      <th align="center" style="width: 125px;">
        <a href="http://www.magedu.com">
          <img src="https://cdn.spug.cc/img/magedu-logo.jpeg" width="115px"><br>
          <sub>马哥教育</sub><br>
          <sub>IT人高薪职业学院</sub>
        </a>
      </th>
    </tr>
  </thead>
</table>

## 开发者群
#### 关注Spug运维公众号加微信群、QQ群、获取最新产品动态
<div >
   <img src="https://cdn.spug.cc/img/spug-club.jpg" width = "300" height = "300" alt="spug-qq" align=center />
<div>
  
## License & Copyright
[AGPL-3.0](https://opensource.org/licenses/AGPL-3.0)
