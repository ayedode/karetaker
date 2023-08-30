# karetaker

Automated Garbage Collection for Docker and Kubernetes.

## Usage

### With Docker

```bash
docker run --detach -v /var/run/docker.sock:/var/run/docker.sock ghcr.io/ayedode/karetaker:main
```

Or with docker-compose:

```bash
docker-compose up -d
```

### With Kubernetes

```bash
kubectl apply -f https://raw.githubusercontent.com/ayedode/karetaker/main/manifests.yml
```

## Configuration

### Environment Variables

| EnvVar | Default | Description |
| ------ | ------- | ----------- |
| KARETAKER_MODE | `""` | Mode, `docker` or `kubernetes` |
| KARETAKER_INTERVAL | `3600` | How long Karetaker waits between two cleanup cycles |
