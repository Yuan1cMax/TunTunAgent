# TunTunAgent

基于 Dify 工作流 + FastAPI 网关构建的囤囤鼠售前智能客服系统，面向游戏租号/售前咨询场景，支持意图分流、RAG 知识问答、导购推荐、Redis 缓存加速和友好兜底。

## 在线体验

- 前端入口页：`http://101.43.56.2:8888`

## 项目亮点

- 多意图分流：先识别用户意图，再进入对应处理链路，避免一个模型同时承担分类和生成
- 知识库问答：RAG + `qwen3-rerank` 重排，提升售前问答命中率
- 商品导购：支持价格区间、关键词筛选与结果推荐，适合售前选品和账户筛选场景
- 缓存加速：对高频问题做 Redis 缓存，降低重复请求的 LLM 成本
- 并发控制：通过互斥锁避免重复查询和并发击穿
- 回答清洗：对模型输出做 `think` 标签清理，保持前台回答更干净

## 技术栈

| 层级 | 技术 |
|---|---|
| 工作流编排 | Dify Advanced Chat |
| 后端网关 | FastAPI (Python) |
| 缓存层 | Redis |
| 意图分流 | deepseek-v4-flash |
| 知识问答 | deepseek-v4-pro + qwen3-rerank |
| 导购接口 | FastAPI / HTTP API |
| 部署 | 腾讯云 Ubuntu 22.04 + Nginx |

## 核心流程

```text
用户请求
  -> FastAPI 网关 (guide.py :8002)
  -> Redis 缓存代理 (cache_proxy.py :8005)
  -> Dify 工作流
      -> 意图分流 (deepseek-v4-flash)
      -> CONSULT  -> RAG 知识库问答
      -> SHOPPING -> 商品导购推荐
      -> HUMAN    -> 转人工兜底
      -> QUERY REWRITE -> 查询改写
      -> think 标签清理
```

## 当前状态

这个项目已经达到可演示状态，当前保留的核心能力包括：

- 咨询、订单查询、售后分流
- RAG + 重排的知识问答
- Redis 缓存和并发控制
- 导购推荐与友好兜底
- 开场白与快捷问题

## 目录说明

```text
project/
├── guide.py
├── cache_proxy.py
├── index.html
└── README.md
```

## 开发说明

- 当前仓库已是 GitHub 仓库本地副本，可直接 `git add` / `git commit` / `git push`
- 这是公开展示版仓库，建议保持项目名、README 和展示页的描述一致
- 如果后续要调整 GitHub 仓库名，记得同步更新本地 `origin` 地址和所有外链

