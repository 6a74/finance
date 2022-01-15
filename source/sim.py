#!/usr/bin/env python3

import argparse

from tabulate import tabulate

import taxes as tm
import ult


def calculate_assets(
        taxable,
        traditional,
        roth,
        rate_of_return,
        years_until_transition_to_pretax_contributions,
        current_age,
        age_of_retirement,
        age_to_start_rmds,
        age_of_death,
        roth_conversion_amount,
        income,
        yearly_income_raise,
        max_income,
        age_of_marriage,
        spending,
        yearly_401k_normal_contribution_limit,
        yearly_401k_total_contribution_limit,
        yearly_ira_contribution_limit,
        ira_contribution_catch_up,
        ira_contribution_catch_up_age,
        do_mega_backdoor_roth,
        working_state,
        retirement_state,
        dependents,
        debug=True,
        print_summary=False
    ):
    """This function will simulate the state of finances for each year until you
    die, depending on the inputs of course!
    """

    def debug_print(line):
        if debug:
            print(line)

    assert current_age <= 115
    assert current_age < age_of_death
    assert yearly_401k_total_contribution_limit >= yearly_401k_normal_contribution_limit

    #
    # Input parameters:
    #
    params_table = []
    params_header = ["Name", "Value"]
    params_table.append(["Starting Age", current_age])
    params_table.append(["Age of Death", age_of_death])
    params_table.append(["Age of Marriage", age_of_marriage])
    params_table.append(["Age of Retirement", age_of_retirement])
    params_table.append(["Age to Start RMDs", age_to_start_rmds])
    params_table.append(["Starting Income", income])
    params_table.append(["Max Income", max_income])
    params_table.append(["Yearly Rate of Return", rate_of_return])
    params_table.append(["Yearly Income Raise", yearly_income_raise])
    params_table.append(["Starting Taxable Balance", taxable])
    params_table.append(["Starting Roth Balance", roth])
    params_table.append(["Starting Traditional Balance", traditional])
    params_table.append(["Yearly Roth Conversion Amount", roth_conversion_amount])
    params_table.append(["Years to Prefer Roth Contributions", years_until_transition_to_pretax_contributions])
    params_table.append(["Yearly Spending", spending])
    params_table.append(["Yearly 401k Pre-tax Limit", yearly_401k_normal_contribution_limit])
    params_table.append(["Yearly 401k Contribution Limit", yearly_401k_total_contribution_limit])
    params_table.append(["Yearly IRA Contribution Limit", yearly_ira_contribution_limit])
    params_table.append(["Do Mega-Backdoor Roth After Tax-Advantaged Limit?", do_mega_backdoor_roth])

    debug_print(
        tabulate(
            params_table,
            params_header,
            tablefmt="fancy_grid",
            numalign="right",
            floatfmt=",.2f"
        )
    )

    #
    # These are life-time counters.
    #
    total_contributions_traditional = 0
    total_contributions_roth = 0
    total_contributions_taxable = 0
    total_taxes = 0

    #
    # The initial values for life events.
    #
    married = age_of_marriage < current_age
    retired = False
    prefer_roth = True

    def num_dependents(current_age):
        if dependents is None:
            return 0
        count = 0
        for dep in dependents:
            #
            # Taxpayers may be able to claim the child tax credit if they have a
            # qualifying child under the age of 17. Part of this credit can be
            # refundable, so it may give a taxpayer a refund even if they don't
            # owe any tax.
            #
            if dep <= current_age and (dep + 17) > current_age:
                count += 1
        return count

    if married:
        yearly_ira_contribution_limit *= 2
    if current_age > ira_contribution_catch_up_age:
        yearly_ira_contribution_limit += ira_contribution_catch_up

    table = []
    header = [
        "Age",
        "Married",
        "Retired",
        "Taxable",
        "Traditional",
        "Roth",
        "Total Assets",
        "RMD",
        "Spending",
        "401k Cont.",
        "IRA Cont.",
        "Taxable Cont.",
        "Taxable Wdrl.",
        "Trad Cont.",
        "Trad Wdrl.",
        "Roth Cont.",
        "Roth Wdrl.",
        "Total Roth Cont.",
        "Gross Income",
        "MAGI",
        "Saver's Credit",
        "Dependents",
        "State",
        "State Tax",
        "Federal Tax",
        "Tax %",
        "Total Taxes"
    ]

    #
    # Iterate over each year in our life.
    #
    for year in range(age_of_death - current_age + 1):

        ########################################################################
        # Life Events
        ########################################################################

        if current_age == age_of_retirement:
            retired = True
        if current_age == age_of_marriage:
            married = True
            yearly_ira_contribution_limit *= 2
        if year >= years_until_transition_to_pretax_contributions:
            prefer_roth = False

        if current_age == ira_contribution_catch_up_age:
            yearly_ira_contribution_limit += ira_contribution_catch_up
            if married:
                yearly_ira_contribution_limit += ira_contribution_catch_up

        #
        # If we have a different state set.
        #
        current_state = retirement_state if retired else working_state

        ########################################################################
        # Retirement Contribution Calculations
        ########################################################################

        tax_deductions = 0
        this_years_income = 0
        this_years_roth_conversion = 0

        # Withdrawals:
        roth_withdrawal = 0
        taxable_withdrawal = 0
        traditional_withdrawal = 0

        # Contributions:
        roth_contribution = 0
        taxable_contribution = 0
        traditional_contribution = 0
        contribution_401k = 0
        contribution_ira = 0

        #
        # Calcuate how much tax advantaged space we have. This will be our total
        # contribution limit for this year.
        #
        tax_advantaged_space = yearly_ira_contribution_limit
        if do_mega_backdoor_roth:
            tax_advantaged_space += yearly_401k_total_contribution_limit
        else:
            tax_advantaged_space += yearly_401k_normal_contribution_limit

        #
        # These variables are used to binary search the maximum contribution.
        #
        minimum_contribution = 0
        total_contribution_limit = tax_advantaged_space
        maximum_contribution = tax_advantaged_space

        #
        # Alright, if we're still working, we can make some tax-advantaged
        # contributions. If there is a traditional contribution, it will lower
        # our taxes and, as a result, will allow us to contribute more! This is
        # why we must binary search the maximum contribution.
        #
        if current_age < age_of_retirement:
            this_years_income = income

            #
            # We'll break out of this once the ideal amount is found.
            #
            while True:
                contribution_401k = 0
                contribution_ira = 0
                traditional_contribution = 0
                roth_contribution = 0

                #
                # Calculate 401k contribution.
                #
                if prefer_roth:
                    contribution_401k += min(min(
                        this_years_income,
                        yearly_401k_normal_contribution_limit
                    ), total_contribution_limit)
                    roth_contribution += contribution_401k
                else:
                    contribution_401k += min(min(
                        this_years_income,
                        yearly_401k_normal_contribution_limit
                    ), total_contribution_limit)
                    traditional_contribution += contribution_401k

                #
                # Calcuate IRA contribution. If there are no tax deductions for
                # the traditional IRA (because our income is too high) we might
                # as well contribute to Roth.
                #
                contribution_ira = min(min(
                    this_years_income,
                    yearly_ira_contribution_limit
                ), total_contribution_limit - contribution_401k)

                would_be_agi_if_trad = (
                    this_years_income
                    - traditional_contribution
                    - contribution_ira
                )

                #
                # TODO: Maybe add a more granular approach, rather than an all
                # or nothing approach.
                #
                if prefer_roth or not tm.fully_tax_deductible_ira(
                        would_be_agi_if_trad, married):
                    roth_contribution += contribution_ira
                else:
                    traditional_contribution += contribution_ira

                #
                # Calculate Mega-Backdoor Roth contribution, if applicable.
                #
                if do_mega_backdoor_roth:
                    after_tax_contribution = min(min(
                        this_years_income,
                        yearly_401k_total_contribution_limit
                    ), total_contribution_limit - contribution_401k - contribution_ira)
                    contribution_401k += after_tax_contribution
                    roth_contribution += after_tax_contribution

                tax_deductions = traditional_contribution

                #
                # We know about all of our tax deductions, we can accurately
                # calculate our taxes now.
                #
                taxable_income = this_years_income - tax_deductions
                fica_tax = tm.calculate_fica_tax(this_years_income, married)
                federal_income_tax = tm.calculate_federal_income_tax(
                    taxable_income,
                    married,
                    num_dependents(current_age)
                )
                federal_income_tax -= tm.calculate_savers_credit(
                    taxable_income,
                    traditional_contribution + roth_contribution,
                    married
                )
                state_tax = tm.calculate_state_tax(
                    taxable_income,
                    married,
                    current_state,
                    num_dependents(current_age)
                )

                #
                # We can't have negative taxes. The saver's credit could
                # potentially be more than our total federal income tax.
                #
                federal_income_tax = max(federal_income_tax, 0)

                result = (
                    this_years_income
                    - spending
                    - fica_tax
                    - federal_income_tax
                    - state_tax
                    - traditional_contribution
                    - roth_contribution
                )

                #
                # Binary search our way to the ideal contribution. We want the
                # result to be zero, which will happen when there is no leftover
                # money after taxes.
                #
                if result > 0:
                    if total_contribution_limit == tax_advantaged_space:
                        break
                    minimum_contribution = total_contribution_limit
                    total_contribution_limit = (
                        minimum_contribution
                        + maximum_contribution
                    )/2
                elif result < 0:
                    if total_contribution_limit == 0:
                        break
                    maximum_contribution = total_contribution_limit
                    total_contribution_limit = (
                        minimum_contribution
                        + maximum_contribution
                    )/2
                else:
                    break

            total_contributions_traditional += traditional_contribution
            traditional += traditional_contribution
            total_contributions_roth += roth_contribution
            roth += roth_contribution
        else:
            #
            # We have retired. While we're retired, but before RMDs, let's do
            # some rollovers from our traditional to Roth accounts. This will
            # allow the money to grow tax free in Roth accounts.
            #
            if current_age < age_to_start_rmds:
                roth_conversion_amount = min(traditional, roth_conversion_amount)
                this_years_income = roth_conversion_amount
                roth_contribution += roth_conversion_amount
                total_contributions_roth += roth_conversion_amount
                roth += roth_conversion_amount
                traditional_withdrawal = roth_conversion_amount
                traditional -= roth_conversion_amount
                this_years_roth_conversion = roth_conversion_amount

        ########################################################################
        # Required Minimum Distribution (RMD) Calculations
        ########################################################################

        if current_age >= age_to_start_rmds:
            #
            # The best amount here is the amount that's included in the standard
            # deduction, and therefore would not be taxed. We should always take
            # at least that amount if possible.
            #
            best_amount = tm.get_standard_deduction(married)
            rmd = traditional/ult.withdrawal_factors[current_age]
            traditional_withdrawal = max(rmd, best_amount)
            if traditional_withdrawal >= traditional:
                traditional_withdrawal = traditional
            traditional -= traditional_withdrawal
            this_years_income += traditional_withdrawal
        else:
            rmd = 0

        ########################################################################
        # Tax Calculations
        ########################################################################

        taxable_income = this_years_income - tax_deductions

        #
        # When calculating the FICA tax, we must not include retirement
        # distributions. These have already been taxed by FICA.
        #
        fica_tax = tm.calculate_fica_tax(
            this_years_income - traditional_withdrawal,
            married
        )

        #
        # Calculate the federal taxes. This includes the federal income tax and
        # FICA (social security and medicare) tax.
        #
        federal_income_tax = tm.calculate_federal_income_tax(
            taxable_income,
            married,
            num_dependents(current_age)
        )

        #
        # Calculate saver's credit. This provides tax credits if you are low
        # income and made retirement contributions.
        #
        savers_credit = 0
        if not retired:
            savers_credit = tm.calculate_savers_credit(
                taxable_income,
                traditional_contribution + roth_contribution,
                married
            )

        #
        # Apply saver's credit. Credits cannot result in negative taxes.
        #
        federal_income_tax = max(federal_income_tax - savers_credit, 0)

        #
        # Finally, calculate state taxes, if any.
        #
        state_tax = tm.calculate_state_tax(
            taxable_income,
            married,
            current_state,
            num_dependents(current_age)
        )

        this_years_taxes = (
            federal_income_tax
            + fica_tax
            + state_tax
        )

        total_taxes += this_years_taxes

        ########################################################################
        # Excess/Insufficent Funds
        ########################################################################

        #
        # First, calculate the difference between our income and spending.
        #
        difference = (
            this_years_income
            - this_years_taxes
            - spending
            - taxable_contribution
            - roth_contribution
            - traditional_contribution
        )

        leftover = max(0, difference)
        needed = abs(min(difference, 0))

        #
        # If we have excess funds, put it into taxable.
        #
        if leftover:
            taxable += leftover
            total_contributions_taxable += leftover
            taxable_contribution += leftover

        if needed:
            #
            # We need money for spending expenses. The order of operations are
            # as follows. They are in order of best to worst.
            #
            # (1) First, take from taxable. Tax rates should be low. This will
            #     give time for Roth contributions to continue to grow tax free.
            #
            # (2) Next, if we still need more, take from the current Roth
            #     contribution for this year. This could be the Mega-Backdoor
            #     Roth conversion contribution.
            #
            # (3) Next, if we still need more, withdrawal Roth contributions
            #     made in previous years. These withdrawals will not be
            #     penalized.
            #
            if needed and taxable > 0:
                withdrawal = needed
                while True:
                    withdrawal = min(withdrawal, taxable)
                    ltcg = (taxable - total_contributions_taxable) * (withdrawal/taxable)
                    this_years_income += withdrawal
                    ltcg_taxes = tm.calculate_federal_income_tax(this_years_income, married, ltcg=ltcg, just_ltcg=True)
                    leftover = withdrawal - ltcg_taxes

                    taxable_withdrawal = withdrawal
                    taxable -= withdrawal
                    total_contributions_taxable -= (withdrawal - ltcg)
                    needed -= withdrawal
                    break

            if needed and roth_contribution:
                to_take = min(needed, roth_contribution)
                roth_contribution -= to_take
                total_contributions_roth -= to_take
                roth -= to_take
                this_years_roth_conversion = max(
                    0, this_years_roth_conversion - to_take
                )
                needed -= to_take

            if needed and total_contributions_roth:
                roth_withdrawal = min(needed, total_contributions_roth)
                total_contributions_roth -= roth_withdrawal
                roth -= roth_withdrawal
                needed -= roth_withdrawal

            #
            # TODO: Clean this entire "needed" section up. We need to pay taxes
            # on tradtional withdrawals.
            #
            if needed and traditional and current_age >= 60:
                to_take = min(needed, traditional)
                traditional_withdrawal += to_take
                traditional -= to_take
                needed -= to_take
                # What is this???
                total_taxes += needed

            if needed and roth and current_age >= 60:
                to_take = min(needed, roth)
                roth_withdrawal += to_take
                roth -= to_take
                needed -= to_take

            if needed:
                raise Exception("not enough money for expenses")

        #
        # Calculate the effective tax rate.
        #
        try:
            tax_rate = (this_years_taxes/this_years_income) * 100
        except ZeroDivisionError:
            tax_rate = 0

        #
        # Calculate our savings rate. This is pre-tax.
        #
        try:
            savings_rate = (
                taxable_contribution
                + traditional_contribution
                + roth_contribution
            ) / this_years_income
        except ZeroDivisionError:
            savings_rate = 0

        #
        # We have finished the year. Add an entry to the table. This will get
        # printed when the simulation is over.
        #
        table.append([
            current_age,
            married,
            retired,
            float(taxable),
            float(traditional),
            float(roth),
            float(taxable + roth + traditional),
            float(rmd),
            float(spending),
            float(contribution_401k),
            float(contribution_ira),
            float(taxable_contribution),
            float(taxable_withdrawal),
            float(traditional_contribution),
            float(traditional_withdrawal),
            float(roth_contribution),
            float(roth_withdrawal),
            float(total_contributions_roth),
            float(this_years_income),
            float(taxable_income),
            float(savers_credit),
            int(num_dependents(current_age)),
            current_state,
            float(state_tax),
            float(federal_income_tax + fica_tax),
            float(tax_rate),
            float(total_taxes)
        ])

        #
        # Happy new year! It's the end of the year. Apply return, give
        # yourself a pay raise, and happy birthday!
        #
        current_age += 1
        if current_age > age_of_death:
            break

        traditional *= rate_of_return
        roth *= rate_of_return
        taxable *= rate_of_return

        if current_age < age_of_retirement:
            income *= yearly_income_raise
            if max_income:
                income = min(income, max_income)

    #
    # We have finished the simulation. Print the table.
    #
    debug_print(
        tabulate(
            table,
            header,
            tablefmt="fancy_grid",
            numalign="right",
            floatfmt=",.2f"
        )
    )

    #
    # Do not sell stocks before death. They get a "step up in basis" meaning the
    # basis changes. The heir will only be responsible for gains after
    # inheritance.
    #
    total_assets = roth + traditional + taxable
    estate_tax = tm.calculate_estate_tax(total_assets)

    #
    # This calculates the minimum tax your heir will be expected to pay as a
    # result of RMDs. This assumes they have no income.
    #
    # TODO: If the user specifies a dependent, use that to determine the
    # difference in age, rather than a hardcoded value. To simplify our
    # calculation, we can assume everything goes to the eldest child.
    #
    taxes_for_heir = tm.calculate_minimum_remaining_tax_for_heir(
        traditional,
        age_of_death - 30
    )

    total_taxes += estate_tax
    total_taxes += taxes_for_heir

    #
    # Print a summary of things.
    #
    summary_table = []
    summary_header = ["Field", "Value at Death"]
    summary_table.append(["Taxable", taxable])
    summary_table.append(["Traditional", traditional])
    summary_table.append(["Roth", roth])
    summary_table.append(["Min Tax for Heir", taxes_for_heir])
    summary_table.append(["Estate Taxes", estate_tax])
    summary_table.append(["Total Taxes", total_taxes])
    summary_table.append(["Total Assets", total_assets])
    summary_table.append(["Total Assets After Taxes", total_assets - total_taxes])
    summary_table.append(["Tax/Asset Ratio", total_taxes/total_assets])

    if debug or print_summary:
        print(
            tabulate(
                summary_table,
                summary_header,
                tablefmt="fancy_grid",
                numalign="right",
                floatfmt=",.2f"
            )
        )

    return total_assets - total_taxes

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Calculate RMDs",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        "--current-age",
        help="Your current age",
        required=False,
        type=int,
        default=38
    )
    parser.add_argument(
        "--income",
        help="Your current income",
        required=False,
        type=float,
        default=63179
    )
    parser.add_argument(
        "--max-income",
        help="Define an income ceiling",
        required=False,
        type=float,
        default=0
    )
    parser.add_argument(
        "--principal-taxable",
        help="Starting balance for taxable (after-tax) accounts?",
        required=False,
        type=float,
        default=0
    )
    parser.add_argument(
        "--principal-traditional",
        help="Starting balance for traditional (pre-tax) accounts?",
        required=False,
        type=float,
        default=0
    )
    parser.add_argument(
        "--principal-roth",
        help="Starting balance for Roth (after-tax) accounts?",
        required=False,
        type=float,
        default=0
    )
    parser.add_argument(
        "--rate-of-return",
        help="What is the long-term rate of return?",
        required=False,
        type=float,
        default=1.04
    )
    parser.add_argument(
        "--yearly-401k-normal-contribution-limit",
        help="What is the normal 401k contribution limit?",
        required=False,
        type=float,
        default=19500
    )
    parser.add_argument(
        "--yearly-401k-total-contribution-limit",
        help="What is the total 401k contribution limit?",
        required=False,
        type=float,
        default=58000
    )
    parser.add_argument(
        "--yearly-ira-contribution-limit",
        help="What is the IRA contribution limit?",
        required=False,
        type=float,
        default=6000
    )
    parser.add_argument(
        "--ira-contribution-catch-up",
        help="How much extra can you contribute at this age?",
        required=False,
        type=float,
        default=1000
    )
    parser.add_argument(
        "--ira-contribution-catch-up-age",
        help="At what age can you do extra catch up contributions?",
        required=False,
        type=int,
        default=50
    )
    parser.add_argument(
        "--do-mega-backdoor-roth",
        help="What is the IRA contribution limit?",
        required=False,
        action='store_true',
        default=False
    )
    parser.add_argument(
        "--years-to-wait",
        help="Years to wait before doing Traditional (pre-tax) contributions",
        metavar="YEARS",
        required=False,
        type=int,
        default=0
    )
    parser.add_argument(
        "--age-of-retirement",
        help="When do you plan to retire?",
        required=False,
        type=int,
        default=60
    )
    parser.add_argument(
        "--working-state",
        help="What state will you work in?",
        required=False,
        choices=tm.states.keys(),
        default='TX'
    )
    parser.add_argument(
        "--retirement-state",
        help="What state will you retire in?",
        required=False,
        choices=tm.states.keys(),
        default='TX'
    )
    parser.add_argument(
        "--age-to-start-rmds",
        help="When do RMDs start?",
        required=False,
        type=int,
        default=72
    )
    parser.add_argument(
        "--age-of-death",
        help="When do you plan on dying?",
        required=False,
        type=int,
        default=79
    )
    parser.add_argument(
        "--yearly-income-raise",
        help="How much of a raise are you expecting each year?",
        required=False,
        type=float,
        default=1.02
    )
    parser.add_argument(
        "--age-of-marriage",
        help="When will you get married?",
        required=False,
        type=int,
        default=30
    )
    parser.add_argument(
        "--spending",
        help="How much do you spend each year?",
        required=False,
        type=float,
        default=30000
    )
    parser.add_argument(
        "--add-dependent",
        help="Your age when dependent is to be added. This option can be used multiple times.",
        required=False,
        type=int,
        metavar="AGE",
        action='append'
    )
    parser.add_argument(
        "--verbose",
        help="Do things and talk more",
        action="store_true"
    )

    args = parser.parse_args()

    #
    # Calculate the most efficient Roth conversion amount.
    #
    most_assets = 0
    roth_conversion_amount = 0
    best_roth_conversion_amount = 0

    #
    # There's a chance we could die before retirement.
    #
    if args.age_of_death > args.age_of_retirement:
        for x in range(1000):
            assets = calculate_assets(
                args.principal_taxable,
                args.principal_traditional,
                args.principal_roth,
                args.rate_of_return,
                args.years_to_wait,
                args.current_age,
                args.age_of_retirement,
                args.age_to_start_rmds,
                args.age_of_death,
                roth_conversion_amount, # this is our variable
                args.income,
                args.yearly_income_raise,
                args.max_income,
                args.age_of_marriage,
                args.spending,
                args.yearly_401k_normal_contribution_limit,
                args.yearly_401k_total_contribution_limit,
                args.yearly_ira_contribution_limit,
                args.ira_contribution_catch_up,
                args.ira_contribution_catch_up_age,
                args.do_mega_backdoor_roth,
                args.working_state,
                args.retirement_state,
                args.add_dependent,
                debug=False
            )
            if assets > most_assets:
                best_roth_conversion_amount = roth_conversion_amount
                most_assets = assets
            if assets < most_assets:
                break
            roth_conversion_amount += 1000

    #
    # Now that we know all of the variables, run the simulation.
    #
    calculate_assets(
        args.principal_taxable,
        args.principal_traditional,
        args.principal_roth,
        args.rate_of_return,
        args.years_to_wait,
        args.current_age,
        args.age_of_retirement,
        args.age_to_start_rmds,
        args.age_of_death,
        best_roth_conversion_amount,
        args.income,
        args.yearly_income_raise,
        args.max_income,
        args.age_of_marriage,
        args.spending,
        args.yearly_401k_normal_contribution_limit,
        args.yearly_401k_total_contribution_limit,
        args.yearly_ira_contribution_limit,
        args.ira_contribution_catch_up,
        args.ira_contribution_catch_up_age,
        args.do_mega_backdoor_roth,
        args.working_state,
        args.retirement_state,
        args.add_dependent,
        args.verbose,
        True
    )
