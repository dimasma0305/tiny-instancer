## Config docker

`/etc/docker/daemon.json` add this:

```json
{
  "default-address-pools": [
    {
      "base": "100.64.0.0/10",
      "size": 24
    }
  ]
}
```

If chatgpt did the math right, this should limit the amount of networks to 16384, which is ~8192 instances worst case.

Restart the docker afterwards.
