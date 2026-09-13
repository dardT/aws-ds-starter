// Serveur de suivi MLflow managé — tranche 3 du socle.
//
// Décision D8 : le J3 compare des exécutions entre elles, ce que la data capture seule ne
// permet pas. Un serveur MLflow managé par SageMaker évite d'en héberger un, mais il est
// facturé À L'HEURE dès sa création, qu'on l'utilise ou non — d'où `create_mlflow`, laissé
// à false.
//
// Disponibilité vérifiée le 28/07/2026 : l'API `list_mlflow_tracking_servers` répond en
// eu-west-3. Le risque identifié en D8 — repli sur un MLflow local si la région ne le
// proposait pas — est donc levé.
//
// UN SEUL serveur pour toute la promotion, avec une expérience par groupe
// (`qc-<TEAM_ID>`). Six serveurs coûteraient six fois plus pour la même chose, et le lab
// n'a pas besoin d'isolation ici : voir les runs des autres groupes est plutôt utile en
// formation.

resource "aws_iam_role" "mlflow" {
  count = var.create_mlflow ? 1 : 0

  name        = "qc-promo-mlflow"
  description = "Rôle du serveur de suivi MLflow. Lit et écrit les artefacts dans le bucket de la promo."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "sts:AssumeRole"
        Principal = { Service = "sagemaker.amazonaws.com" }
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      },
    ]
  })
}

// Les artefacts MLflow (modèles, graphiques, fichiers de métriques) vont dans un bucket
// dédié plutôt que dans celui d'un groupe : ils appartiennent à la promotion, et un bucket
// de groupe expire à 30 jours.
resource "aws_s3_bucket" "mlflow" {
  count = var.create_mlflow ? 1 : 0

  bucket = "qc-promo-mlflow-${local.account_suffix}"

  // Même raison que pour les buckets de groupe : sans cela, socle-destroy s'arrête sur
  // un bucket non vide.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "mlflow" {
  count = var.create_mlflow ? 1 : 0

  bucket = aws_s3_bucket.mlflow[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_iam_role_policy" "mlflow" {
  count = var.create_mlflow ? 1 : 0

  name = "qc-promo-mlflow"
  role = aws_iam_role.mlflow[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ArtefactsMLflow"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.mlflow[0].arn,
          "${aws_s3_bucket.mlflow[0].arn}/*",
        ]
      },
    ]
  })
}

resource "aws_sagemaker_mlflow_tracking_server" "promo" {
  count = var.create_mlflow ? 1 : 0

  tracking_server_name = "qc-promo-mlflow"
  role_arn             = aws_iam_role.mlflow[0].arn
  artifact_store_uri   = "s3://${aws_s3_bucket.mlflow[0].id}/artifacts/"

  // Small : le plus petit format. Le J3 enregistre quelques dizaines d'exécutions, pas
  // des milliers.
  tracking_server_size = var.mlflow_server_size

  // Enregistre automatiquement les modèles SageMaker dans le registre MLflow. Évite au
  // binôme d'écrire le code de liaison, qui n'est pas le sujet du J3.
  automatic_model_registration = true

  tags = {
    Name = "qc-promo-mlflow"
  }
}
