// Réseau de la promotion — tranche 2 du socle.
//
// Un seul VPC pour tout le monde. L'isolation entre groupes ne passe pas par le réseau
// mais par IAM et par le préfixe de nommage : six VPC coûteraient six passerelles NAT,
// pour une séparation dont la formation n'a pas besoin.
//
// Deux zones de disponibilité parce que l'ALB en exige au moins deux. Une seule
// passerelle NAT, volontairement non redondante : c'est un environnement de formation,
// et la NAT est l'un des postes de coût dominants.

locals {
  // Deux AZ suffisent, et les figer par index rend le plan stable d'une exécution à
  // l'autre. Sans cela, un changement d'ordre dans la réponse d'AWS déplacerait des
  // sous-réseaux.
  azs = slice(data.aws_availability_zones.disponibles.names, 0, 2)

  public_subnets = {
    "a" = { cidr = "10.0.0.0/24", az = local.azs[0] }
    "b" = { cidr = "10.0.1.0/24", az = local.azs[1] }
  }

  private_subnets = {
    "a" = { cidr = "10.0.10.0/24", az = local.azs[0] }
    "b" = { cidr = "10.0.11.0/24", az = local.azs[1] }
  }
}

data "aws_availability_zones" "disponibles" {
  state = "available"
}

resource "aws_vpc" "promo" {
  cidr_block = var.vpc_cidr

  // Les deux sont nécessaires aux endpoints d'interface et à la résolution des noms
  // de services AWS depuis les tâches ECS.
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "qc-promo-vpc"
  }
}

// --- Sous-réseaux -------------------------------------------------------------------

resource "aws_subnet" "public" {
  for_each = local.public_subnets

  vpc_id            = aws_vpc.promo.id
  cidr_block        = each.value.cidr
  availability_zone = each.value.az

  // Nécessaire à la passerelle NAT, qui doit joindre la passerelle internet.
  map_public_ip_on_launch = true

  tags = {
    Name = "qc-promo-public-${each.key}"
    Tier = "public"
  }
}

resource "aws_subnet" "private" {
  for_each = local.private_subnets

  vpc_id            = aws_vpc.promo.id
  cidr_block        = each.value.cidr
  availability_zone = each.value.az

  tags = {
    Name = "qc-promo-private-${each.key}"
    Tier = "private"
  }
}

// --- Sortie vers internet ------------------------------------------------------------

resource "aws_internet_gateway" "promo" {
  vpc_id = aws_vpc.promo.id

  tags = {
    Name = "qc-promo-igw"
  }
}

resource "aws_eip" "nat" {
  domain = "vpc"

  tags = {
    Name = "qc-promo-nat"
  }

  depends_on = [aws_internet_gateway.promo]
}

// UNE passerelle NAT, pas une par zone. Elle est facturée à l'heure ET au gigaoctet
// traité, et c'est le deuxième poste de coût du socle après l'endpoint SageMaker.
// Le prix d'une panne de zone est ici une formation interrompue une demi-journée, pas
// un incident de production : la redondance ne se justifie pas.
resource "aws_nat_gateway" "promo" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public["a"].id

  tags = {
    Name = "qc-promo-nat"
  }

  depends_on = [aws_internet_gateway.promo]
}

// --- Tables de routage ---------------------------------------------------------------

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.promo.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.promo.id
  }

  tags = {
    Name = "qc-promo-public"
  }
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

// Une seule table privée, partagée par les deux zones, puisqu'il n'y a qu'une NAT.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.promo.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.promo.id
  }

  tags = {
    Name = "qc-promo-private"
  }
}

resource "aws_route_table_association" "private" {
  for_each = aws_subnet.private

  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}

// --- Endpoint S3, toujours créé -------------------------------------------------------
//
// Un endpoint de type gateway est GRATUIT et évite que le trafic S3 des tâches ECS et
// des machines de travail passe par la NAT, qui elle est facturée au gigaoctet. Sur des
// jeux de données et des images de conteneur, l'économie est réelle.

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.promo.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"

  route_table_ids = [
    aws_route_table.private.id,
    aws_route_table.public.id,
  ]

  tags = {
    Name = "qc-promo-s3"
  }
}

// --- Endpoints d'interface — appliqués par défaut -------------------------------------
//
// Décision D9, amendée le 30/07/2026 (revue technique, point 7) : `enable_vpc_endpoints`
// vaut `true` par défaut. La crainte d'origine — les endpoints suppriment la sortie
// internet — confondait deux gestes : c'est la suppression de la NAT qui coupe internet,
// pas l'ajout des endpoints. NAT et endpoints coexistent : le trafic AWS passe par les
// endpoints, le reste (SECOM, uv, git clone) par la NAT.
//
// Et l'environnement cible ne garantit PAS la sortie internet : sans endpoints, une
// coupure de NAT ou un réseau l'entreprise fermé arrête tout le parcours AWS. Le mode
// hors-ligne complet (préchargement du dépôt, de SECOM, des wheels et des images) est
// documenté dans infra/ami/README.md.
//
// Remettre `false` reste possible pour un compte bac à sable où le coût horaire des
// endpoints d'interface compte plus que la résilience réseau.

locals {
  services_interface = var.enable_vpc_endpoints ? toset([
    "ecr.api",           // authentification au registre
    "ecr.dkr",           // téléchargement des couches d'image
    "sagemaker.api",     // piloter jobs et endpoints (create/describe) depuis les workstations
    "sagemaker.runtime", // invocation de l'endpoint depuis l'application
    "bedrock-runtime",   // appels du modèle par l'agent du J2
    "logs",              // journalisation des tâches
    "sts",               // résolution de l'identité par boto3
    "ssm",               // Session Manager : le SEUL accès aux workstations (D2)
    "ssmmessages",       // canal de données des sessions SSM
    "ec2messages",       // canal de contrôle de l'agent SSM
  ]) : toset([])
}

resource "aws_security_group" "endpoints" {
  count = var.enable_vpc_endpoints ? 1 : 0

  name        = "qc-promo-endpoints"
  description = "Endpoints d interface - HTTPS depuis le VPC uniquement"
  vpc_id      = aws_vpc.promo.id

  ingress {
    description = "HTTPS depuis le VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = {
    Name = "qc-promo-endpoints"
  }
}

resource "aws_vpc_endpoint" "interface" {
  for_each = local.services_interface

  vpc_id              = aws_vpc.promo.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [for s in aws_subnet.private : s.id]
  security_group_ids  = [aws_security_group.endpoints[0].id]
  private_dns_enabled = true

  tags = {
    Name = "qc-promo-${replace(each.key, ".", "-")}"
  }
}
