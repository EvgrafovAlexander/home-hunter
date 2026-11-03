"""Контролируемый перенос однозначных пометок из личных оценок в исключения рынка."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from listings.models import MarketExclusion
from listings.services.market_exclusions import exclude_from_market, unfinished_review_candidates


class Command(BaseCommand):
    help = 'Перенести оценку 1 с пометкой об отсутствии отделки; по умолчанию только показать ID.'

    def add_arguments(self, parser):
        parser.add_argument('--author-id', type=int, required=True)
        parser.add_argument('--actor-id', type=int)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        candidates = unfinished_review_candidates(options['author_id'])
        self.stdout.write(f'Найдено: {len(candidates)}; ID: {", ".join(str(r.listing_id) for r in candidates)}')
        if not options['apply']:
            return
        actor = get_user_model().objects.filter(pk=options['actor_id'], is_staff=True, is_active=True).first()
        if actor is None:
            raise CommandError('Для применения нужен --actor-id активного сотрудника.')
        count = 0
        with transaction.atomic():
            for review in candidates:
                # История возврата защищает от повторного включения отменённого переноса.
                if MarketExclusion.objects.filter(listing_id=review.listing_id).exists():
                    continue
                _, created = exclude_from_market(actor, review.listing_id, MarketExclusion.Reason.UNFINISHED,
                                                  f'Перенесено из оценки #{review.pk}: отсутствует отделка.')
                count += created
        self.stdout.write(f'Создано исключений: {count}')
