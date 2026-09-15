# Setting-up remote development (VSCode, Zed, Pycharm)


## Prerequisites

1. Install AWS cli [install documentation](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html#getting-started-install-instructions)

2. Install the SSM plugin for AWS cli [install documentation](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)

3. Verify your identity, and attempt logging in the workstation of your group:

```shell
aws sts get-caller-identity
```

You can get your workstation instance Id using this query:

```shell
export TEAM_ID=g01  # feed your own group id
aws ec2 describe-instances \
  --query "Reservations[].Instances[?Tags[?Key==\`Team\` && Value==\`$TEAM_ID\`]].InstanceId" \
  --output text
```

```shell
aws ssm start-session --target <instance-id>
```

## Windows without the AWS CLI (boto3-only workaround)

**Who this is for:** Windows users whose corporate policy forbids installing the AWS CLI
(and therefore the `session-manager-plugin`), but who have an IAM access key / secret key
and can install Python packages. If you were able to follow the Prerequisites above, skip
this section — the `ProxyCommand` path is the officially supported one.

This path needs the same permissions as the AWS CLI path (`ssm:StartSession`,
`ec2:DescribeInstances`) — it is the same API, called from Python instead of the CLI.

### 1. Prerequisite: `uv`

The whole repo already runs its Python through [`uv`](https://docs.astral.sh/uv/), so the
only thing to install is `uv` itself — e.g. `pip install uv`, or the official installer.
Nothing else: no AWS CLI, no separate binary.

### 2. Credentials

boto3 reads them directly from the project's `.env` file (see `.env.example`). No
`aws configure`, no AWS CLI:

```txt
AWS_REGION=eu-west-3
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
TEAM_ID=g01
OWNER_EMAIL=you@example.com
```

`TEAM_ID` and `OWNER_EMAIL` are required by this repo's config object, not by AWS — the
tunnel script uses `TEAM_ID` to find *your* team's workstation.

### 3. Open the tunnel

```shell
make ssh-tunnel
```

(or `uv run scripts/ssm_ssh_tunnel.py`, with `--local-port <port>` to change the port.)

The script resolves your team's instance ID via boto3, then hands over to
[`pyssm-client`](https://pypi.org/project/pyssm-client/), run on the fly through `uvx`,
which opens a **local TCP port** (`2222` by default) tunnelled to port 22 of the instance.

> `pyssm-client` is a small third-party project — it is **not** maintained by AWS. Using
> it here is an informed trade-off against being unable to connect at all.

**This process stays running**: it *is* the tunnel. Leave that terminal open and press
`Ctrl-C` when you're done. Connect from another terminal (or your IDE).

### 4. SSH config for this path

Generate an SSH key as described in [Setting-up SSH](#setting-up-ssh) below, then add:

```txt
Host ec2-safran
    HostName localhost
    Port 2222
    User ubuntu
    IdentityFile ~/.ssh/ec2-safran-ssm
```

There is **no `ProxyCommand`** here: the tunnel script already plays that role, from its
own terminal. SSH simply connects to the local port it opened.

Then follow [Last steps](#last-steps) below to install your public key on the workstation
— that part is identical whichever tunnelling method you used.

### Known limitation: KMS-encrypted sessions

`pyssm-client` does not implement KMS encryption. If the training account has a KMS key
configured under *Systems Manager → Session Manager → Preferences*, the tunnel will hang
and the SSH connection will never come up (no clear error message — it just sits there).

If that happens, don't keep retrying: ask the trainer whether session encryption is
enforced. If it is, this path cannot work and you must fall back to the AWS CLI +
`session-manager-plugin` route documented above.

## Setting-up SSH

### MacOS/Linux

1. Generate an ssh key locally

```shell
ssh-keygen -t ed25519 -f ~/.ssh/ec2-safran-ssm
```

> Don't forget to give it a **passphrase**

2. Edit the local `~/.ssh/config` file by adding the corresponding host:

```txt
Host ec2-safran
    HostName 
    User ubuntu
    IdentityFile ~/.ssh/ec2-safran-ssm
    ProxyCommand sh -c "aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p'"
```

### Windows

Windows 10/11 ship with the OpenSSH client, so no extra install is needed — run the following from **PowerShell**.

1. Generate an ssh key locally

```powershell
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\ec2-safran-ssm
```

> Don't forget to give it a **passphrase**

2. Edit the local `%USERPROFILE%\.ssh\config` file (create it if it doesn't exist) by adding the corresponding host:

```txt
Host ec2-safran
    HostName 
    User ubuntu
    IdentityFile ~/.ssh/ec2-safran-ssm
    ProxyCommand aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters "portNumber=%p"
```

> The `ProxyCommand` above runs directly, unlike the `sh -c "..."` wrapper used on MacOS/Linux, since Windows' OpenSSH has no POSIX shell to invoke.

### Last steps

1. Login to the workstation and activate the ubuntu user:

```shell
aws ssm start-session --target <instance-id>
sudo -iu ubuntu
```

2. Create the ssh configuration onto the workstation:

```shell
sudo mkdir -p /home/ubuntu/.ssh
sudo chmod 700 /home/ubuntu/.ssh
```

Get the content of the local ssh public key:

```shell
cat ~/.ssh/ec2-safran-ssm.pub
```

Add it to `~/.ssh/authorized_keys`:

```shell
echo "<content of the ssh public key>" >> ~/.ssh/authorized_keys
```

Then

```shell
chmod 600 ~/.ssh/authorized_keys
```


That's good now, you can ssh directly into the VM from your local IDE/terminal using `ssh ec2-safran`

#### VSCode

1. Install the Remote-SSH Extension
2. Connect to the server - [documentation here](https://code.visualstudio.com/docs/remote/ssh)
