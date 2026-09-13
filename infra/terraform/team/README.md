# Module `team/` — ce que chaque binôme applique

Le socle est appliqué une fois par le formateur. Ce module-ci est appliqué **par chaque
binôme**, sur son propre état.

| Module | Appliqué par | Contient |
| --- | --- | --- |
| `socle/` | le formateur, une fois | VPC, ALB + règles et groupes de cibles par binôme, clusters, dépôts ECR, rôles, buckets |
| `team/` | chaque binôme | sa définition de tâche, son service ECS, son groupe de logs |

## Pourquoi deux modules

Six binômes qui lancent `terraform apply` sur le même état se bloquent mutuellement, ou
écrasent le travail des autres. Chaque groupe a donc sa propre clé d'état,
`team/<TEAM_ID>/terraform.tfstate`, dans le bucket commun.

Conséquence : un binôme ne peut pas détruire le socle, donc pas sa propre machine de
travail, avec le `terraform destroy` du J3.

## Utilisation

```bash
make team-init  TEAM_ID=g01     # une fois
make team-plan  TEAM_ID=g01     # à relire
make team-apply TEAM_ID=g01
```

L'image doit avoir été poussée dans ECR avant l'`apply` : le service ECS échouerait à
démarrer une tâche dont l'image n'existe pas.

## Ce que le J3 modifie ici

`desired_count`, `task_cpu`, `task_memory`, `log_retention_days`, puis ajout d'une alarme
CloudWatch. C'est exactement le périmètre du lab Terraform.
