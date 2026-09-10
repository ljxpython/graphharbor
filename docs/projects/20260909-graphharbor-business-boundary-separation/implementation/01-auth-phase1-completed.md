# Phase 1: GraphHarbor 业务耦合清理 - 已完成

## 执行时间
2026-09-09

## 完成任务

### Task 1.1: 从 auth.py 移除 DelegationJWTValidator 和 RuntimePolicy ✅

**修改文件**: `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/auth.py`

**删除的类和函数**:
- `RuntimePolicy` 数据类（原 33-37 行）
- `_policy_name()`, `_policy_names()` 辅助函数
- `parse_runtime_policy()` - policy 解析函数
- `validate_policy_overrides()` - policy 白名单校验函数
- `_policy_claims()` - policy 序列化函数
- `JWKSCache` 类 - JWKS 密钥缓存（原 387-416 行）
- `DelegationJWTValidator` 类 - JWT 验证器（原 418-503 行）

**修改的函数**:
- `sign_runtime_context()`: 删除 `policy` 参数
- `verify_runtime_context_envelope()`: 返回类型从 `tuple[dict, RuntimePolicy | None]` 改为 `dict[str, Any]`
- `verify_runtime_context()`: 简化返回，直接返回 dict 而非解包 tuple
- `Principal.from_claims()`: 删除 policy 解析逻辑（原 322-342 行）
- `Principal.from_auth_user()`: 删除 policy 解析逻辑（原 364-380 行）
- `Principal` dataclass: 删除 `policy: RuntimePolicy | None = None` 字段

**清理 imports**:
- 删除 `urllib.request`（JWKSCache 使用）

### Task 1.2: 修改 PrincipalMiddleware 使用 auth_handler ✅

**修改文件**: `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/auth.py`

**PrincipalMiddleware 修改**:
- 构造函数 `validator` 参数改为 `Any` 类型，标记为 deprecated
- 删除 `self.validator` 实例变量
- `__call__()` 方法删除 validator 分支（原 463-467 行）
- 仅保留 `auth_handler` 认证路径
- 无 auth_handler 且不允许匿名时返回 401 错误

### Task 1.3: 修改 server.py 移除 DelegationJWTValidator ✅

**修改文件**: `libs/langhost/src/langhost/server.py`

**修改内容**:
- 删除 `DelegationJWTValidator` 的 import（原 26 行）
- 删除 `validator = DelegationJWTValidator.from_env()` 逻辑（原 842-844 行）
- `PrincipalMiddleware` 初始化时不再传递 `validator` 参数（原 863 行）

**修改文件**: `libs/langhost/src/langhost/core_api.py`

**修改内容**:
- 删除 `validate_policy_overrides` 的 import（原 24 行）
- 删除 policy 校验逻辑（原 1028-1044 行）:
  - `policy = getattr(principal, "policy", None)`
  - 生产环境 policy 必需检查
  - `validate_policy_overrides()` 调用

### Task 1.4: 标记或删除相关测试 ✅

**修改文件**: `libs/langgraph-runtime-pg/tests/test_production_contract.py`

**标记跳过的测试**（添加 `@pytest.mark.skip` 装饰器）:
1. `test_delegation_principal_and_hs256_validation` - DelegationJWTValidator 迁移
2. `test_production_delegation_requires_runtime_policy` - RuntimePolicy 迁移
3. `test_delegation_policy_is_bound_to_principal_and_runtime_context` - RuntimePolicy 迁移
4. `test_delegation_jwt_rejects_algorithm_and_refreshes_rotated_key` - DelegationJWTValidator 和 JWKSCache 迁移

**修复的测试**:
- `test_custom_auth_user_is_preserved_in_signed_worker_context`: 
  - 修改 `verify_runtime_context_envelope()` 调用，不再解包两个值（原 546 行）
  - 改为 `restored = verify_runtime_context_envelope(...)` 

## 测试验证

**通过的测试**:
```bash
uv run pytest libs/langgraph-runtime-pg/tests/test_production_contract.py -k "auth or principal or context" -v
```
- ✅ test_runtime_context_rejects_unknown_nested_and_top_level_claims
- ✅ test_api_principal_producer_preserves_correlation  
- ✅ test_custom_auth_user_is_preserved_in_signed_worker_context
- ✅ test_runtime_context_is_signed_to_one_run_and_scope
- ✅ test_runtime_context_requires_matching_issuer_and_audience
- ✅ test_runtime_context_does_not_reuse_external_jwt_issuer_and_audience
- ✅ test_production_custom_auth_does_not_require_builtin_jwt_config

**跳过的测试**: 4 个（delegation/policy 相关）

## 影响分析

### 破坏性变更
1. **API 变更**:
   - `sign_runtime_context()` 不再接受 `policy` 参数
   - `verify_runtime_context_envelope()` 返回类型从 tuple 改为 dict
   - `Principal` 不再有 `policy` 字段
   - `PrincipalMiddleware` 构造函数 `validator` 参数标记为 deprecated

2. **删除的公开符号**:
   - `RuntimePolicy` 类
   - `DelegationJWTValidator` 类
   - `JWKSCache` 类
   - `validate_policy_overrides()` 函数

3. **环境变量**（不再使用）:
   - `GRAPHHARBOR_JWT_ISSUER`
   - `GRAPHHARBOR_JWT_AUDIENCE`
   - `GRAPHHARBOR_JWT_JWKS_URL`
   - `GRAPHHARBOR_JWT_SHARED_SECRET`
   - `GRAPHHARBOR_JWT_ALGORITHMS`
   - `GRAPHHARBOR_JWT_LEEWAY_SECONDS`

### 向后兼容性
- ⚠️ **不兼容**: 任何直接使用 `DelegationJWTValidator` 或 `RuntimePolicy` 的代码需要迁移
- ✅ **兼容**: 使用 `auth_handler` 的代码不受影响
- ✅ **兼容**: RuntimeContext 签名/验证逻辑保持向后兼容（只是不再携带 policy）

## 后续任务

Phase 2 和 Phase 3（在 ai-agent-platform 仓库）:
- [ ] Task 2.1: 在 runtime-service 创建 DelegationJWTValidator
- [ ] Task 2.2: 实现 authenticate() handler
- [ ] Task 2.3: 配置 langgraph.json auth
- [ ] Task 3.1: runtime-service 调用 sign_runtime_context
- [ ] Task 3.2: 修改 production_worker.py
- [ ] Task 3.3: 更新 langgraph.json metadata
