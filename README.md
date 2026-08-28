# Nextwaves RFID Portal Gate Service

Đây là repository phát hành sản phẩm headless dành cho khách hàng. Runtime
nghiệp vụ/model được đóng gói thành CPython 3.11 Linux x86_64 extension (`.so`);
repository này không chứa source Python được bảo vệ, model plaintext, database,
calibration key hay credential của khách hàng.

Phiên bản hiện tại: **1.0.0-rc1**. Đây là release candidate để chạy canary và
hardware acceptance trên đúng gate NR155 trước khi phát hành `1.0.0`.

## Kiến trúc giao tiếp

- REST/HTTPS `8443`: command, query transaction và calibration.
- gRPC/TLS `50051`: status và live event stream không durable.
- MQTT 5/TLS: event nghiệp vụ authoritative, QoS 1, transactional SQLite
  outbox và retry sau reconnect.
- USB NR155 `303a:4002`: reader `if00`, sensor `if02` qua stable
  `/dev/serial/by-id`.

Mỗi container chỉ quản lý một physical gate và không được scale nhiều replica.

## Nội dung repository

```text
runtime/       protected cp311 Linux runtime
deploy/        Compose, systemd, udev, preflight và acceptance checklist
contracts/     REST OpenAPI, gRPC proto và MQTT JSON Schema
release/       protected-runtime manifest
scripts/       product checksum và leakage scanner
Dockerfile     runtime-only image build; không biên dịch protected source
```

`PRODUCT_SHA256SUMS` khóa toàn bộ file sản phẩm. Kiểm tra trước khi build:

```sh
python3 scripts/verify_product.py .
python3 scripts/scan_headless_release.py \
  --root runtime \
  --manifest release/protected_modules_headless.json
```

Sau khi copy đầy đủ `runtime/` đã bảo vệ, maintainer tạo/cập nhật
manifest bằng lệnh sau:

```sh
python3 scripts/verify_product.py . --write
```

Chế độ `--write` sẽ từ chối ghi nếu runtime thiếu, có symlink, secret,
plaintext protected source, native artifact không phải ELF x86_64 hoặc file ngoài
allowlist. File checksum chỉ được publish atomically sau khi scan thành công.

## Lấy image phát hành

Tag phát hành phải khớp `v${VERSION}`; với release candidate hiện tại là
`v1.0.0-rc1`. Tag `v*` chạy GitHub Actions để build Linux/amd64, tạo
SBOM/provenance, push image lên GHCR, ký digest bằng Cosign và tạo GitHub
Release chứa deployment bundle. Luôn lấy giá trị `image=...@sha256:...` trong
`RELEASE_MANIFEST.txt`; không deploy tag mutable.

Workflow tách quyền theo trust boundary: pull request và `main` chỉ có
`contents: read`; job tag duy nhất có quyền ghi GitHub Release/GHCR và xin OIDC.
Trước lần release đầu, tạo GitHub Environment `production`, cấu hình required
reviewer và bật Actions access tới GHCR. Protected branch `main` nên yêu cầu
hai check `verify_product` và `ci_image`.

Nếu GHCR package là private, đăng nhập trên host bằng token chỉ có quyền
`read:packages`:

```sh
echo "$GHCR_READ_TOKEN" | docker login ghcr.io -u CUSTOMER_USER --password-stdin
docker pull ghcr.io/hyzie/rfid-portal-gate-service@sha256:RELEASE_DIGEST
```

Có thể build tại chỗ từ protected runtime (không cần source nội bộ):

```sh
docker build -t ghcr.io/hyzie/rfid-portal-gate-service:v1.0.0-rc1 .
```

Sau khi push image, lấy digest bằng `docker inspect` và cấu hình
`GATE_IMAGE=<repository>@sha256:<digest>`.

## Deploy lên Linux gate host

Yêu cầu:

- Linux x86_64 chạy Docker Engine trực tiếp và Docker Compose v2;
- đúng hai interface NR155 stable by-id (`if00`, `if02`);
- broker MQTT 5/TLS do khách hàng quản lý;
- một thư mục data persistent và sáu secret file theo
  [deploy/README.md](deploy/README.md).

Quy trình rút gọn:

```sh
sudo install -d -m 0750 /opt/nextwaves-gate/deploy
sudo cp deploy/* /opt/nextwaves-gate/deploy/
sudo install -m 0644 deploy/nextwaves-gate.service \
  deploy/nextwaves-gate-hotplug.service /etc/systemd/system/
sudo install -m 0644 deploy/99-nextwaves-rfid.rules /etc/udev/rules.d/
cd /opt/nextwaves-gate/deploy
sudo cp gate.env.example gate.env
# Điền image digest, gate ID, by-id devices, broker và secret paths.
sudo systemctl daemon-reload
sudo udevadm control --reload-rules
sudo sh -c 'set -a; . ./gate.env; set +a; sh ./wait-for-devices.sh'
sudo systemctl enable --now nextwaves-gate.service
sudo REQUIRE_READY=0 sh ./validate-running.sh
```

Gate Linux phải calibration lại; state DPAPI từ Windows không portable. Thực
hiện đầy đủ [deploy/ACCEPTANCE.md](deploy/ACCEPTANCE.md), sau đó chạy
`validate-running.sh` với `REQUIRE_READY=1`.

## Contract và vận hành

- [REST OpenAPI](contracts/openapi.json)
- [gRPC proto](contracts/gate_stream.proto)
- [MQTT delivery contract](contracts/MQTT.md)
- [MQTT event schema](contracts/mqtt/event-envelope.schema.json)
- [Deployment/backup/restore](deploy/README.md)
- [Hardware acceptance](deploy/ACCEPTANCE.md)

REST và gRPC dùng chung bearer token, TLS tối thiểu 1.2. Mọi REST mutation bắt
buộc có `X-Operator-ID` và `Idempotency-Key`. Không expose service trực tiếp ra
Internet; chỉ mở trong VLAN/VPN khách hàng. Không commit `gate.env`, secret,
database hoặc customer data vào repository này.
