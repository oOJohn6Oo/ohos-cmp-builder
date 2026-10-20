# CMP 构建控制台

静态 HTML/CSS/JavaScript，无打包依赖。在仓库根目录运行：

```sh
python3 -m http.server 8765 --bind 127.0.0.1 --directory docs
```

访问 http://127.0.0.1:8765/ 。线上地址：https://cmp.jon6.org 。

`credential-public-key.json` 保存发布公钥；对应私钥只配置在仓库的 `MAVEN_CREDENTIAL_PRIVATE_KEY` Secret 中。Supabase `submit-build` 需允许 `https://cmp.jon6.org` 的 CORS 来源。
