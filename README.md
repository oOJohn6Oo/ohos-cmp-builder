# OHOS CMP Builder

从 GitCode 拉取 CMP-Core 和 CMP 源码，构建并发布 CMP 1.12，同时生成 HAR 和发布信息。

构建页面：https://cmp.jon6.org 。前端位于 `docs/`，修改前端后由 `pages.yml` 部署。

## 配置

仓库 Secret `MAVEN_CREDENTIAL_PRIVATE_KEY` 保存解密私钥；对应公钥放在 `docs/credential-public-key.json`。私钥不得提交到仓库。

页面填写发布仓库 HTTPS URL、账号和密码。凭据在浏览器加密后保存在私有任务记录中，在 Actions 中解密一次并用于原生发布，任务结束后清除密文。密文绑定构建配置，有效期 24 小时；过期需重新提交。

## 脚本

- `scripts/validate.py`：领取任务、输入与凭据校验、回写状态。
- `scripts/build.py`：源码准备、SDK、版本配置；使用 Core/CMP 原生 Gradle 发布流程并构建 HAR，`--debug` 仅发布到 Maven Local。
- `scripts/latest_dependencies.py`：通过 Nexus API 查询 maven-public，按固定前缀、版本倒序筛选正式版；逐个检查 OHOS ARM64 坐标，缺失则尝试下一版本。
- `scripts/bump_versions.py`：统一依赖版本、发布版本及 HAR 包版本，可单独运行，支持 `--dry-run`。
- `scripts/publish.py`：生成发布信息 Markdown 和 HAR 下载链接。

## 运行

网页调用 `submit-build`，函数创建任务并追加 `history.md`；该 commit 触发 `build.yml`，每次提交只有一条运行记录。Actions 使用 GitHub 签发的短期身份凭证领取任务，完成后回写状态。

工作流根据提交模式执行正式构建或 **Debug**。Debug 完整编译 Core、CMP、HAR，仅发布到 Runner 的 Maven Local，不需要发布仓库地址或账号密码；最终同样保留 Markdown 和 HAR。

后缀以数字结尾，例如 `dev.01`，对应版本 `1.12.0-dev.01`。

依赖支持 `default`、`latest`、`custom`；自定义时增加 `version` 字段。省略的依赖使用分支默认值，支持 Kotlin、Skiko、Coroutines（Core/CMP）、Serialization、AtomicFU、DateTime、Okio。

## 下载

构建完成后，在 Actions 运行页面下载：

- `compose-<version>-<run-id>`：HAR，保留 30 天。
- `release-info-<version>-<run-id>`：发布信息 Markdown，包含 HAR 下载链接，保留 30 天。
