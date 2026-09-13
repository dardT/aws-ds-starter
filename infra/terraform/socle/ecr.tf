// Registre d'images — tranche 2 du socle.
//
// Un dépôt par groupe. Le binôme y pousse l'image de son application au J2 ; c'est ECS
// qui la tire ensuite, en endossant le rôle d'exécution de tâche.

resource "aws_ecr_repository" "team" {
  for_each = toset(var.teams)

  name = "qc-${each.key}-app"

  // Une image poussée deux fois sous le même tag doit remplacer la précédente : au J2 un
  // binôme reconstruit son image cinq ou six fois sous le tag `latest`. En mode IMMUTABLE
  // le deuxième `docker push` échouerait.
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    // Gratuit, et donne un vrai sujet de discussion au J2 : l'image de base traîne
    // toujours des CVE, et la question « lesquelles bloquent une mise en production ? »
    // se pose devant un résultat réel plutôt qu'en théorie.
    scan_on_push = true
  }

  // Sans ceci, `make socle-destroy` échoue sur tout dépôt contenant encore une image, et
  // le formateur doit les vider à la main, dépôt par dépôt, en fin de formation.
  force_delete = true

  tags = {
    Team = each.key
  }
}

// Dix images conservées par dépôt. Six binômes qui reconstruisent une dizaine de fois
// chacun, c'est une soixantaine d'images de plusieurs centaines de mégaoctets stockées
// pour rien. La règle purge les plus anciennes automatiquement.
resource "aws_ecr_lifecycle_policy" "team" {
  for_each = aws_ecr_repository.team

  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Ne conserver que les dix dernières images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = { type = "expire" }
      },
    ]
  })
}
