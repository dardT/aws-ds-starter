// Un bucket de données par binôme (§14).
//
// Le compte est partagé par toute la promotion : l'isolation ne repose donc pas sur des
// comptes séparés mais sur le préfixe de nommage `qc-<TEAM_ID>` et sur des politiques IAM
// conditionnées par ce préfixe. C'est aussi le support pédagogique du J1 sur le privilège
// minimal — les binômes lisent réellement la politique qui les contraint.

resource "aws_s3_bucket" "team" {
  for_each = local.team_buckets

  bucket = each.value

  // Sans ceci, `make socle-destroy` échoue en fin de formation sur « BucketNotEmpty :
  // You must delete all versions in the bucket ». Le versioning étant actif, un objet
  // supprimé laisse ses versions derrière lui, et Terraform ne les efface pas.
  //
  // Le formateur devrait alors vider six buckets à la main, version par version.
  // Constaté le 28/07/2026 au premier socle-destroy réel.
  //
  // C'est une ressource DESTRUCTRICE : un `socle-destroy` lancé par erreur emporte les
  // données des binômes. C'est assumé — cette commande n'existe que pour la fin de
  // formation, et seul le formateur y a accès.
  force_destroy = true

  tags = {
    Team = each.key
  }
}

// Le versioning protège d'un écrasement accidentel pendant les labs. Il sert aussi au J3 :
// comparer une baseline à une capture suppose que la baseline n'a pas été remplacée par
// mégarde entre-temps.
resource "aws_s3_bucket_versioning" "team" {
  for_each = aws_s3_bucket.team

  bucket = each.value.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "team" {
  for_each = aws_s3_bucket.team

  bucket = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

// Contrôle n°4 du preflight : il vérifie que ce blocage est TOTAL. Un blocage partiel est
// traité comme un échec, pas comme un avertissement.
resource "aws_s3_bucket_public_access_block" "team" {
  for_each = aws_s3_bucket.team

  bucket = each.value.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

// Transport chiffré obligatoire (§14). Refuser explicitement le HTTP en clair est plus
// robuste que de compter sur les clients pour utiliser HTTPS.
resource "aws_s3_bucket_policy" "team_tls_only" {
  for_each = aws_s3_bucket.team

  bucket = each.value.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "RefuserTransportNonChiffre"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          each.value.arn,
          "${each.value.arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })

  // Sans cette dépendance, la politique peut être appliquée avant le blocage des accès
  // publics et l'apply échoue de façon intermittente.
  depends_on = [aws_s3_bucket_public_access_block.team]
}

// Garde-fou de coût : les objets des labs disparaissent au bout de 30 jours. Sans cela,
// un bucket oublié après la formation continue d'être facturé indéfiniment.
resource "aws_s3_bucket_lifecycle_configuration" "team" {
  for_each = aws_s3_bucket.team

  bucket = each.value.id

  rule {
    id     = "supprimer-objets-des-labs"
    status = "Enabled"

    filter {}

    expiration {
      days = var.data_retention_days
    }

    // Le versioning étant actif, supprimer un objet ne fait que le masquer. Sans cette
    // règle, les versions non courantes resteraient facturées pour toujours.
    noncurrent_version_expiration {
      noncurrent_days = 7
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 3
    }
  }
}

// Préfixes créés à vide (§11). S3 n'a pas de vrais répertoires : ces objets de taille nulle
// servent uniquement à ce que la console affiche l'arborescence attendue, et à ce qu'un
// binôme voie où déposer ses fichiers sans avoir à deviner.
resource "aws_s3_object" "prefixes" {
  for_each = local.team_prefixes

  bucket  = aws_s3_bucket.team[each.value.team].id
  key     = "${each.value.prefix}/"
  content = ""

  tags = {
    Team = each.value.team
  }
}
